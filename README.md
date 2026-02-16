# AI Grading Assistant

A web-based application that automates the grading of student submissions using Google's Gemini AI. Upload student PDFs and a grading rubric, and get comprehensive grading reports in both PDF and CSV formats.

## Features

- 🎓 **AI-Powered Grading**: Uses Google Gemini 2.0 Flash for intelligent grading
- 📁 **Bulk Processing**: Upload multiple PDFs in a ZIP file or single PDF submissions
- 📋 **Custom Rubrics**: Define your own detailed grading criteria
- 📄 **Multiple Output Formats**: Get results as both detailed PDF reports and structured CSV data
- 🌐 **Web Interface**: Modern, responsive web interface for easy use
- 🔒 **Secure**: API keys are handled securely and not stored

## Setup

1. **Install Dependencies**
   ```bash
   # Activate virtual environment
   source venv/bin/activate
   
   # Install required packages
   pip install -r requirements.txt
   ```

2. **Get Google Gemini API Key**
   - Visit [Google AI Studio](https://aistudio.google.com/)
   - Create an API key for Gemini
   - Keep this key secure - you'll enter it in the web interface

3. **Run the Application**
   ```bash
   python app.py
   ```

4. **Access the Web Interface**
   - Open your browser to `http://localhost:5000`
   - Enter your Gemini API key
   - Upload your grading rubric and student submissions
   - Click "Start Grading" and wait for results

## Usage

1. **Prepare Your Files**
   - Create a detailed grading rubric with point values and criteria
   - Collect student submissions as PDF files
   - Optionally, zip multiple PDFs together

2. **Upload and Grade**
   - Enter your Google Gemini API key
   - Paste your grading rubric in the text area
   - Upload your PDF file(s)
   - Click "Start Grading"

3. **Download Results**
   - PDF Report: Detailed grading feedback for each student
   - CSV Report: Structured data with student names, grades, and comments

## File Structure

```
├── app.py              # Main Flask application
├── templates/
│   └── index.html      # Web interface
├── requirements.txt    # Python dependencies
├── uploads/           # Temporary file storage
├── results/           # Generated reports
└── README.md          # This file
```

## API Endpoints

- `GET /` - Main web interface
- `POST /api/grade` - Process grading request
- `GET /api/download/<session_id>/<file_type>` - Download results

## Security Notes

- API keys are only used during the request and not stored
- Uploaded files are automatically cleaned up after processing
- All file uploads are validated for security

## Troubleshooting

- **"Failed to initialize AI client"**: Check your API key is valid
- **"No PDF files found"**: Ensure your ZIP contains PDF files
- **Large file uploads**: Files over 100MB are not supported
- **Slow processing**: Grading time depends on file size and count

## Original Pipeline

This web application is based on a Jupyter notebook pipeline that processes student submissions using the Google Gemini API for automated grading.
