import os
import pathlib
import csv
import io
from flask import Flask, render_template, request, send_file, flash, redirect, url_for
from werkzeug.utils import secure_filename
from google import genai
from google.genai import types
from fpdf import FPDF
import tempfile
import zipfile

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configuration
UPLOAD_FOLDER = 'uploads'
RESULTS_FOLDER = 'results'
ALLOWED_EXTENSIONS = {'pdf'}

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)

# Initialize Gemini client
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is required")

client = genai.Client(api_key=GEMINI_API_KEY)

# Default grading rubric
DEFAULT_RUBRIC = """
Grade this exam relative to the below rubric. Return your response as a short note with any deductions and total points scored along with the student name.
Total Points: 100
Submission Requirements (5 points)

Academic integrity statement included and personalized (3 points)
Proper file naming convention with UID (.Rmd and .pdf files) (2 points)

Deductions

Failure to knit Rmd to PDF: -10 points
Using HTML-to-PDF conversion instead of direct knitting: -5 points
Poor code style (not following Tidyverse guidelines): -2 points per major violation
Including install.packages() in Rmd: -3 points

Question 1: dplyr and vehicles dataset (25 points)
Part (a): Unique makers (3 points)

Correct use of dplyr to count unique makers (2 points)
Visible output showing correct answer (1 point)

Part (b): 2014 vehicles count (3 points)

Correct filtering and counting for year 2014 (2 points)
Visible output showing correct answer (1 point)

Part (c): 2014 compact vs midsize mpg (4 points)

Correct filtering by year and class (compact/midsize) (2 points)
Accurate average city mpg calculations for both car types (2 points)

Part (d): 2014 midsize cars by manufacturer (8 points)

Correct filtering for 2014 midsize cars (2 points)
Proper grouping by manufacturer and calculating average city mpg (3 points)
Results arranged in descending order by mpg (2 points)
Clean table output with only required columns (1 point)

Part (e): Multi-year analysis with tidyr (7 points)

Correct filtering for specified years (1994, 1999, 2004, 2009, 2014) (2 points)
Proper grouping and summarizing by manufacturer and year (2 points)
Correct use of tidyr to reshape data (wide format with years as columns) (2 points)
All rows printed and visible in output (1 point)

Question 2: String manipulation and regex (20 points)
Part (a): Data cleaning (8 points)

Removes leading/trailing whitespace correctly (2 points)
Converts to proper title case (2 points)
Removes punctuation except hyphens (2 points)
Output matches expected clean_responses exactly (2 points)

Part (b): Regular expression detection (8 points)

Correct regex to detect "R" mentions (case-insensitive) (3 points)
Correct regex to detect "Python" mentions (case-insensitive) (3 points)
Accurate counting of language mentions (2 points)

Part (c): ID number formatting (7 points)

Correctly identifies 5-digit ID numbers (2 points)
Adds leading zeroes to make all IDs 7 digits (3 points)
Verification shown without printing entire dataset (2 points)

Question 4: S3 Class System (15 points)
Part (a): Rectangle constructor (4 points)

setClass() used correctly with all required slots (3 points)
Proper slot types specified (character, numeric) (1 point)

Part (b): Player methods (6 points)

get_info() generic function created (2 points)
get_info() method returns correctly formatted string (3 points)
Test case included and working (1 point)

Part (c): FootballPlayer subclass and methods (5 points)

FootballPlayer inherits from Player with additional slots (2 points)
score_goal() generic function created (1 point)
score_goal() method increments goals_scored and returns updated object (2 points)
"""

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def grade_pdf(pdf_path, rubric):
    """Grade a single PDF using Gemini API"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=[
                types.Part.from_bytes(
                    data=pathlib.Path(pdf_path).read_bytes(),
                    mime_type='application/pdf',
                ),
                rubric
            ]
        )
        return response.text if hasattr(response, 'text') else response.candidates[0].content.parts[0].text
    except Exception as e:
        return f"Error grading PDF: {str(e)}"

def create_pdf_report(responses, output_path):
    """Create a PDF report from grading responses"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)
    
    for i, response in enumerate(responses):
        pdf.add_page()
        pdf.multi_cell(0, 10, f"Grading Report {i+1}\n\n{response}")
    
    pdf.output(output_path)

def extract_csv_from_pdf(pdf_path):
    """Extract structured CSV data from the grading report PDF"""
    pdf_prompt = """
    Please review the attached PDF with grading reports for every student and standardize them into CSV output. 
    The CSV should have columns for student name, ID, total grade for the assignment, and then only one column for commentary on the whole assignment, including the notes on deductions.
    You should standardize the commentary as well: only provide the amount of points deducted and the reason for each deduction concisely stated, not the full commentary. Emphasis on concise!
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=[
                types.Part.from_bytes(
                    data=pathlib.Path(pdf_path).read_bytes(),
                    mime_type='application/pdf',
                ),
                pdf_prompt
            ]
        )
        return response.text if hasattr(response, 'text') else response.candidates[0].content.parts[0].text
    except Exception as e:
        return f"Error extracting CSV: {str(e)}"

@app.route('/')
def index():
    return render_template('simple_index.html')

@app.route('/grade', methods=['POST'])
def grade_assignments():
    if 'pdf_files' not in request.files:
        flash('No files selected')
        return redirect(url_for('index'))
    
    files = request.files.getlist('pdf_files')
    rubric = request.form.get('rubric', DEFAULT_RUBRIC)
    
    if not files or all(file.filename == '' for file in files):
        flash('No files selected')
        return redirect(url_for('index'))
    
    # Process uploaded files
    uploaded_files = []
    filenames = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)
            uploaded_files.append(filepath)
            filenames.append(filename)
    
    if not uploaded_files:
        flash('No valid PDF files uploaded')
        return redirect(url_for('index'))
    
    # Grade each PDF
    responses = []
    grading_results = []
    for i, pdf_path in enumerate(uploaded_files):
        print(f"Grading file: {os.path.basename(pdf_path)}")
        response = grade_pdf(pdf_path, rubric)
        responses.append(response)
        grading_results.append({
            'filename': filenames[i],
            'content': response
        })
    
    # Create PDF report
    pdf_report_path = os.path.join(RESULTS_FOLDER, 'grading_report.pdf')
    create_pdf_report(responses, pdf_report_path)
    
    # Extract CSV data
    csv_text = extract_csv_from_pdf(pdf_report_path)
    
    # Parse CSV data for display
    csv_data = []
    csv_lines = csv_text.splitlines()
    for line in csv_lines:
        if line.strip():  # Skip empty lines
            # Simple CSV parsing - split by comma but handle quoted fields
            row = [field.strip().strip('"') for field in line.split(',')]
            if row:  # Only add non-empty rows
                csv_data.append(row)
    
    # Clean up uploaded files
    for filepath in uploaded_files:
        try:
            os.remove(filepath)
        except:
            pass
    
    flash(f'Successfully graded {len(uploaded_files)} assignments!')
    return render_template('results.html', 
                         grading_results=grading_results,
                         csv_data=csv_data,
                         csv_text=csv_text,
                         num_files=len(uploaded_files))

@app.route('/download/<filename>')
def download_file(filename):
    filepath = os.path.join(RESULTS_FOLDER, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        flash('File not found')
        return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
