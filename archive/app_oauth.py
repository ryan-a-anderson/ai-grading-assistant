import os
import pathlib
import tempfile
import zipfile
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, flash, session
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.contrib.github import make_github_blueprint, github
from flask_dance.consumer.storage.sqla import OAuthConsumerMixin, SQLAlchemyStorage
from wtforms import StringField, PasswordField, TextAreaField, FileField, SubmitField
from wtforms.validators import InputRequired, Length, ValidationError
from werkzeug.utils import secure_filename
import bcrypt
import pandas as pd
from fpdf import FPDF
import csv
from google import genai
from google.genai import types

app = Flask(__name__)
CORS(app)

# Configuration
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///grading_app_oauth.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# OAuth Configuration - In production, set these as environment variables
app.config['GOOGLE_OAUTH_CLIENT_ID'] = 'your-google-client-id'
app.config['GOOGLE_OAUTH_CLIENT_SECRET'] = 'your-google-client-secret'
app.config['GITHUB_OAUTH_CLIENT_ID'] = 'your-github-client-id'
app.config['GITHUB_OAUTH_CLIENT_SECRET'] = 'your-github-client-secret'

UPLOAD_FOLDER = 'uploads'
RESULTS_FOLDER = 'results'
ALLOWED_EXTENSIONS = {'pdf', 'zip'}

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['RESULTS_FOLDER'] = RESULTS_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

# Centralized API key - in production, store this securely
GEMINI_API_KEY = 'YOUR_API_KEY_HERE'

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth_choice'

# OAuth Blueprints
google_bp = make_google_blueprint(
    client_id=app.config.get('GOOGLE_OAUTH_CLIENT_ID'),
    client_secret=app.config.get('GOOGLE_OAUTH_CLIENT_SECRET'),
    scope=["openid", "email", "profile"]
)
app.register_blueprint(google_bp, url_prefix="/login")

github_bp = make_github_blueprint(
    client_id=app.config.get('GITHUB_OAUTH_CLIENT_ID'),
    client_secret=app.config.get('GITHUB_OAUTH_CLIENT_SECRET'),
    scope="user:email"
)
app.register_blueprint(github_bp, url_prefix="/login")

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.LargeBinary, nullable=True)  # Nullable for OAuth users
    oauth_provider = db.Column(db.String(20), nullable=True)  # 'google', 'github', or None
    oauth_id = db.Column(db.String(100), nullable=True)
    avatar_url = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    assignments = db.relationship('Assignment', backref='user', lazy=True)

class OAuth(OAuthConsumerMixin, db.Model):
    provider_user_id = db.Column(db.String(256), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey(User.id), nullable=False)
    user = db.relationship("User")

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    rubric = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='processing')  # processing, completed, error
    graded_count = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    pdf_report_path = db.Column(db.String(500))
    csv_report_path = db.Column(db.String(500))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# Setup OAuth storage
google_bp.storage = SQLAlchemyStorage(OAuth, db.session, user=current_user)
github_bp.storage = SQLAlchemyStorage(OAuth, db.session, user=current_user)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Forms
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[InputRequired(), Length(min=4, max=20)])
    password = PasswordField('Password', validators=[InputRequired(), Length(min=4, max=20)])
    submit = SubmitField('Login')

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[InputRequired(), Length(min=4, max=20)])
    email = StringField('Email', validators=[InputRequired(), Length(min=6, max=120)])
    password = PasswordField('Password', validators=[InputRequired(), Length(min=4, max=20)])
    submit = SubmitField('Register')

    def validate_username(self, username):
        existing_user_username = User.query.filter_by(username=username.data).first()
        if existing_user_username:
            raise ValidationError('That username already exists. Choose a different one.')

    def validate_email(self, email):
        existing_user_email = User.query.filter_by(email=email.data).first()
        if existing_user_email:
            raise ValidationError('That email address belongs to a different user. Choose a different one.')

class GradingForm(FlaskForm):
    title = StringField('Assignment Title', validators=[InputRequired(), Length(min=1, max=200)])
    rubric = TextAreaField('Grading Rubric', validators=[InputRequired()])
    submissions = FileField('Student Submissions')
    submit = SubmitField('Start Grading')

# Helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def initialize_gemini_client():
    """Initialize Gemini client with centralized API key"""
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        return client
    except Exception as e:
        raise Exception(f"Failed to initialize Gemini client: {str(e)}")

def create_user_from_oauth(provider, user_info):
    """Create a new user from OAuth provider information"""
    username = user_info.get('login') or user_info.get('name') or user_info.get('email', '').split('@')[0]
    email = user_info.get('email')
    
    # Ensure unique username
    base_username = username
    counter = 1
    while User.query.filter_by(username=username).first():
        username = f"{base_username}{counter}"
        counter += 1
    
    user = User(
        username=username,
        email=email,
        oauth_provider=provider,
        oauth_id=str(user_info.get('id')),
        avatar_url=user_info.get('avatar_url')
    )
    db.session.add(user)
    db.session.commit()
    return user

def extract_pdf_files(zip_path, extract_to):
    """Extract PDF files from a ZIP archive"""
    pdf_files = []
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file_info in zip_ref.infolist():
            if file_info.filename.lower().endswith('.pdf') and not file_info.filename.startswith('__MACOSX'):
                zip_ref.extract(file_info, extract_to)
                pdf_files.append(os.path.join(extract_to, file_info.filename))
    return pdf_files

def grade_submissions(client, pdf_files, rubric_text):
    """Grade PDF submissions using Gemini API"""
    responses = []
    for pdf_path in pdf_files:
        try:
            print(f"Grading file: {os.path.basename(pdf_path)}")
            
            with open(pdf_path, 'rb') as pdf_file:
                pdf_data = pdf_file.read()
            
            response = client.models.generate_content(
                model="gemini-2.0-flash-exp", 
                contents=[
                    types.Part.from_bytes(
                        data=pdf_data,
                        mime_type='application/pdf',
                    ),
                    rubric_text
                ]
            )
            responses.append({
                'filename': os.path.basename(pdf_path),
                'response': response
            })
        except Exception as e:
            print(f"Error grading {pdf_path}: {str(e)}")
            responses.append({
                'filename': os.path.basename(pdf_path),
                'error': str(e)
            })
    
    return responses

def create_grading_report_pdf(responses, output_path):
    """Create a PDF report with all grading results"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)
    
    for i, response_data in enumerate(responses):
        pdf.add_page()
        
        if 'error' in response_data:
            text = f"Error grading {response_data['filename']}: {response_data['error']}"
        else:
            response = response_data['response']
            text = response.text if hasattr(response, "text") else response.candidates[0].content.parts[0].text
        
        pdf.multi_cell(0, 10, f"Grading Report {i+1} - {response_data['filename']}\n\n{text}")
    
    pdf.output(output_path)

def standardize_to_csv(client, pdf_report_path, csv_output_path):
    """Convert grading report PDF to standardized CSV format"""
    pdf_prompt = """
    Please review the attached PDF with grading reports for every student and standardize them into CSV output. 
    The CSV should have columns for student name, ID, total grade for the assignment, and then only one column for commentary on the whole assignment, including the notes on deductions.
    You should standardize the commentary as well: only provide the amount of points deducted and the reason for each deduction concisely stated, not the full commentary. Emphasis on concise!
    """
    
    with open(pdf_report_path, 'rb') as pdf_file:
        pdf_data = pdf_file.read()
    
    response = client.models.generate_content(
        model="gemini-2.0-flash-exp", 
        contents=[
            types.Part.from_bytes(
                data=pdf_data,
                mime_type='application/pdf',
            ),
            pdf_prompt
        ]
    )
    
    with open(csv_output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        for line in response.text.splitlines():
            if line.strip():
                writer.writerow([col.strip() for col in line.split(',')])
    
    return response.text

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing_oauth.html')

@app.route('/auth')
def auth_choice():
    return render_template('auth_choice.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.password_hash and bcrypt.checkpw(form.password.data.encode('utf-8'), user.password_hash):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login_oauth.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.hashpw(form.password.data.encode('utf-8'), bcrypt.gensalt())
        new_user = User(
            username=form.username.data,
            email=form.email.data,
            password_hash=hashed_password
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    return render_template('register_oauth.html', form=form)

@app.route('/login/google')
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    
    resp = google.get("/oauth2/v1/userinfo")
    if not resp.ok:
        flash('Failed to fetch user info from Google')
        return redirect(url_for('auth_choice'))
    
    google_info = resp.json()
    
    # Check if user already exists
    user = User.query.filter_by(email=google_info['email']).first()
    
    if not user:
        user = create_user_from_oauth('google', google_info)
    
    login_user(user)
    flash(f'Successfully logged in with Google!')
    return redirect(url_for('dashboard'))

@app.route('/login/github')
def github_login():
    if not github.authorized:
        return redirect(url_for("github.login"))
    
    resp = github.get("/user")
    if not resp.ok:
        flash('Failed to fetch user info from GitHub')
        return redirect(url_for('auth_choice'))
    
    github_info = resp.json()
    
    # Get email separately as it might be private
    email_resp = github.get("/user/emails")
    if email_resp.ok:
        emails = email_resp.json()
        primary_email = next((email['email'] for email in emails if email['primary']), None)
        if primary_email:
            github_info['email'] = primary_email
    
    # Check if user already exists
    user = User.query.filter_by(email=github_info.get('email')).first() if github_info.get('email') else None
    
    if not user:
        user = create_user_from_oauth('github', github_info)
    
    login_user(user)
    flash(f'Successfully logged in with GitHub!')
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    assignments = Assignment.query.filter_by(user_id=current_user.id).order_by(Assignment.created_at.desc()).all()
    return render_template('dashboard_oauth.html', assignments=assignments)

@app.route('/grade', methods=['GET', 'POST'])
@login_required
def grade():
    form = GradingForm()
    if form.validate_on_submit():
        # Create new assignment record
        assignment = Assignment(
            title=form.title.data,
            rubric=form.rubric.data,
            user_id=current_user.id,
            status='processing'
        )
        db.session.add(assignment)
        db.session.commit()
        
        # Process the grading in the background (for now, synchronously)
        try:
            submissions_file = form.submissions.data
            if not submissions_file or not allowed_file(submissions_file.filename):
                flash('Invalid file type. Please upload PDF or ZIP files only.')
                return redirect(url_for('grade'))
            
            # Initialize Gemini client
            client = initialize_gemini_client()
            
            # Save uploaded file
            filename = secure_filename(submissions_file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{assignment.id}_{filename}")
            submissions_file.save(file_path)
            
            # Process submissions
            pdf_files = []
            if filename.lower().endswith('.zip'):
                extract_dir = os.path.join(app.config['UPLOAD_FOLDER'], f'extracted_{assignment.id}')
                os.makedirs(extract_dir, exist_ok=True)
                pdf_files = extract_pdf_files(file_path, extract_dir)
            else:
                pdf_files = [file_path]
            
            if not pdf_files:
                assignment.status = 'error'
                db.session.commit()
                flash('No PDF files found in the uploaded content')
                return redirect(url_for('dashboard'))
            
            # Grade submissions
            responses = grade_submissions(client, pdf_files, form.rubric.data)
            
            # Create results directory for this assignment
            assignment_results_dir = os.path.join(app.config['RESULTS_FOLDER'], str(assignment.id))
            os.makedirs(assignment_results_dir, exist_ok=True)
            
            # Generate PDF report
            pdf_report_path = os.path.join(assignment_results_dir, 'grading_reports.pdf')
            create_grading_report_pdf(responses, pdf_report_path)
            
            # Generate CSV report
            csv_report_path = os.path.join(assignment_results_dir, 'grading_report.csv')
            standardize_to_csv(client, pdf_report_path, csv_report_path)
            
            # Update assignment record
            assignment.status = 'completed'
            assignment.graded_count = len([r for r in responses if 'error' not in r])
            assignment.error_count = len([r for r in responses if 'error' in r])
            assignment.pdf_report_path = pdf_report_path
            assignment.csv_report_path = csv_report_path
            db.session.commit()
            
            # Clean up uploaded files
            os.remove(file_path)
            if filename.lower().endswith('.zip'):
                import shutil
                shutil.rmtree(os.path.join(app.config['UPLOAD_FOLDER'], f'extracted_{assignment.id}'), ignore_errors=True)
            
            flash(f'Grading completed! {assignment.graded_count} submissions graded successfully.')
            return redirect(url_for('dashboard'))
            
        except Exception as e:
            assignment.status = 'error'
            db.session.commit()
            flash(f'An error occurred: {str(e)}')
            return redirect(url_for('dashboard'))
    
    return render_template('grade_oauth.html', form=form)

@app.route('/download/<int:assignment_id>/<file_type>')
@login_required
def download_report(assignment_id, file_type):
    assignment = Assignment.query.filter_by(id=assignment_id, user_id=current_user.id).first_or_404()
    
    try:
        if file_type == 'pdf' and assignment.pdf_report_path:
            return send_file(assignment.pdf_report_path, as_attachment=True, 
                           download_name=f'{assignment.title}_grading_reports.pdf')
        elif file_type == 'csv' and assignment.csv_report_path:
            return send_file(assignment.csv_report_path, as_attachment=True, 
                           download_name=f'{assignment.title}_grading_report.csv')
        else:
            flash('File not found')
            return redirect(url_for('dashboard'))
            
    except Exception as e:
        flash(f'Error downloading file: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/assignment/<int:assignment_id>')
@login_required
def view_assignment(assignment_id):
    assignment = Assignment.query.filter_by(id=assignment_id, user_id=current_user.id).first_or_404()
    return render_template('assignment_detail.html', assignment=assignment)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=8083)
