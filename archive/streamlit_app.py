import streamlit as st
import os
import pathlib
import csv
import io
import tempfile
from google import genai
from google.genai import types
from fpdf import FPDF
import pandas as pd

# Page configuration
st.set_page_config(
    page_title="🎓 AI Grading Assistant",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize Gemini client
@st.cache_resource
def init_gemini_client():
    # Try environment variable first (for local development)
    api_key = os.environ.get("GEMINI_API_KEY")
    
    # If not found, try Streamlit secrets (for cloud deployment)
    if not api_key:
        try:
            api_key = st.secrets["GEMINI_API_KEY"]
        except:
            pass
    
    if not api_key:
        st.error("⚠️ GEMINI_API_KEY not found. Please add it to your environment variables or Streamlit secrets.")
        st.stop()
    return genai.Client(api_key=api_key)

client = init_gemini_client()

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

def grade_pdf(pdf_bytes, filename, rubric):
    """Grade a single PDF using Gemini API"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(
                    data=pdf_bytes,
                    mime_type='application/pdf',
                ),
                rubric
            ]
        )
        return response.text if hasattr(response, 'text') else response.candidates[0].content.parts[0].text
    except Exception as e:
        return f"Error grading {filename}: {str(e)}"

def create_pdf_report(grading_results):
    """Create a PDF report from grading responses"""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=12)
    
    for i, result in enumerate(grading_results):
        pdf.add_page()
        pdf.multi_cell(0, 10, f"Grading Report {i+1}: {result['filename']}\n\n{result['content']}")
    
    # Return PDF as bytes
    return bytes(pdf.output())

def extract_csv_from_reports(grading_results):
    """Extract structured CSV data from grading results"""
    pdf_prompt = """
    Please review the attached grading reports and standardize them into CSV output. 
    The CSV should have columns for student name, ID, total grade for the assignment, and then only one column for commentary on the whole assignment, including the notes on deductions.
    You should standardize the commentary as well: only provide the amount of points deducted and the reason for each deduction concisely stated, not the full commentary. Emphasis on concise!
    """
    
    # Combine all grading results into one text
    combined_text = "\n\n".join([f"File: {result['filename']}\n{result['content']}" for result in grading_results])
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[combined_text + "\n\n" + pdf_prompt]
        )
        return response.text if hasattr(response, 'text') else response.candidates[0].content.parts[0].text
    except Exception as e:
        return f"Error extracting CSV: {str(e)}"

# Main app
def main():
    st.title("🎓 AI Grading Assistant")
    st.markdown("Upload PDF assignments and get AI-powered grading with detailed reports")
    
    # Sidebar for configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        st.markdown("### 📋 Grading Rubric")
        rubric = st.text_area(
            "Edit your grading rubric:",
            value=DEFAULT_RUBRIC,
            height=300,
            help="Customize the grading criteria for your assignments"
        )
        
        st.markdown("### 🔑 API Status")
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            try:
                api_key = st.secrets["GEMINI_API_KEY"]
            except:
                pass
        
        if api_key:
            st.success("✅ Gemini API Key configured")
        else:
            st.error("❌ Gemini API Key missing")
    
    # Main content area
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.header("📄 Upload PDF Assignments")
        uploaded_files = st.file_uploader(
            "Choose PDF files",
            type=['pdf'],
            accept_multiple_files=True,
            help="Upload one or more PDF assignments to grade"
        )
        
        if uploaded_files:
            st.success(f"📁 {len(uploaded_files)} files uploaded successfully!")
            
            # Display uploaded files
            with st.expander("📋 View uploaded files"):
                for file in uploaded_files:
                    st.write(f"• {file.name} ({file.size:,} bytes)")
    
    with col2:
        st.header("🚀 Actions")
        if uploaded_files:
            if st.button("🎯 Start Grading", type="primary", use_container_width=True):
                grade_assignments(uploaded_files, rubric)
        else:
            st.info("Upload PDF files to begin grading")

def grade_assignments(uploaded_files, rubric):
    """Process the uploaded files and display results"""
    
    # Progress tracking
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    grading_results = []
    
    # Grade each PDF
    for i, uploaded_file in enumerate(uploaded_files):
        status_text.text(f"Grading {uploaded_file.name}...")
        progress_bar.progress((i + 1) / len(uploaded_files))
        
        # Read PDF bytes
        pdf_bytes = uploaded_file.read()
        
        # Grade the PDF
        response = grade_pdf(pdf_bytes, uploaded_file.name, rubric)
        
        grading_results.append({
            'filename': uploaded_file.name,
            'content': response
        })
    
    status_text.text("✅ Grading complete!")
    
    # Display results
    display_results(grading_results)

def display_results(grading_results):
    """Display the grading results"""
    
    st.header("📊 Grading Results")
    
    # Summary stats
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📄 Files Processed", len(grading_results))
    with col2:
        st.metric("📊 Reports Generated", "2")
    with col3:
        st.metric("⚡ Status", "Complete")
    
    # Tabs for different views
    tab1, tab2, tab3 = st.tabs(["📄 Individual Reports", "📊 CSV Summary", "📥 Downloads"])
    
    with tab1:
        st.subheader("Detailed Grading Reports")
        for i, result in enumerate(grading_results):
            with st.expander(f"📄 {result['filename']}", expanded=i==0):
                st.text_area(
                    f"Grading feedback for {result['filename']}:",
                    value=result['content'],
                    height=300,
                    disabled=True,
                    key=f"report_{i}"
                )
    
    with tab2:
        st.subheader("CSV Summary")
        
        # Extract CSV data
        with st.spinner("Generating CSV summary..."):
            csv_text = extract_csv_from_reports(grading_results)
        
        # Parse and display CSV
        try:
            # Parse CSV text into rows
            csv_lines = csv_text.splitlines()
            csv_data = []
            for line in csv_lines:
                if line.strip():
                    row = [field.strip().strip('"') for field in line.split(',')]
                    if row:
                        csv_data.append(row)
            
            if csv_data:
                # Create DataFrame
                if len(csv_data) > 1:
                    df = pd.DataFrame(csv_data[1:], columns=csv_data[0])
                    st.dataframe(df, use_container_width=True)
                else:
                    st.write("No structured data available")
                
                # Raw CSV text
                st.subheader("Raw CSV Data")
                st.text_area(
                    "Copy this data to your gradebook:",
                    value=csv_text,
                    height=200,
                    help="You can copy and paste this directly into Excel or Google Sheets"
                )
        except Exception as e:
            st.error(f"Error parsing CSV: {e}")
            st.text_area("Raw CSV Response:", value=csv_text, height=200)
    
    with tab3:
        st.subheader("Download Reports")
        
        # Generate PDF report
        with st.spinner("Generating PDF report..."):
            pdf_bytes = create_pdf_report(grading_results)
        
        # Download buttons
        col1, col2 = st.columns(2)
        
        with col1:
            st.download_button(
                label="📄 Download PDF Report",
                data=pdf_bytes,
                file_name="grading_report.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        
        with col2:
            csv_text = extract_csv_from_reports(grading_results)
            st.download_button(
                label="📊 Download CSV Summary",
                data=csv_text,
                file_name="grading_summary.csv",
                mime="text/csv",
                use_container_width=True
            )
    
    # Option to grade more files
    st.markdown("---")
    if st.button("🔄 Grade More Assignments", type="secondary"):
        st.rerun()

if __name__ == "__main__":
    main()
