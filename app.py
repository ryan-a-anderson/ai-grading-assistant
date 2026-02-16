import os
import pathlib
import tempfile
import zipfile
import csv
import re
import time
import shutil
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
from fpdf import FPDF
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
UPLOAD_FOLDER = "uploads"
RESULTS_FOLDER = "results"
ALLOWED_EXTENSIONS = {"pdf", "zip"}

MAX_ZIP_FILES = 100                     # max PDFs to extract from a ZIP
MAX_FILE_SIZE = 50 * 1024 * 1024        # 50 MB per individual file
MAX_RUBRIC_LENGTH = 50_000              # characters
MAX_WORKERS = 4                         # concurrent Gemini API calls
MAX_RETRIES = 2                         # retries per API call
MAX_RESULTS_AGE_SECONDS = 3600          # auto-cleanup after 1 hour

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["RESULTS_FOLDER"] = RESULTS_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB upload limit

# ---------------------------------------------------------------------------
# Gemini client (lazy singleton)
# ---------------------------------------------------------------------------
_gemini_client = None


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable not set")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_pdf_files(zip_path, extract_to):
    """Extract PDF files from a ZIP archive with safety limits."""
    pdf_files = []
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for file_info in zip_ref.infolist():
            if not file_info.filename.lower().endswith(".pdf"):
                continue
            if file_info.filename.startswith("__MACOSX"):
                continue
            if file_info.file_size > MAX_FILE_SIZE:
                logger.warning(
                    "Skipping %s: %d MB exceeds limit",
                    file_info.filename,
                    file_info.file_size // (1024 * 1024),
                )
                continue
            if len(pdf_files) >= MAX_ZIP_FILES:
                logger.warning("Reached max file count (%d), skipping rest", MAX_ZIP_FILES)
                break
            zip_ref.extract(file_info, extract_to)
            pdf_files.append(os.path.join(extract_to, file_info.filename))
    return pdf_files


def _cleanup_old_results():
    """Remove results directories older than MAX_RESULTS_AGE_SECONDS."""
    try:
        results_path = pathlib.Path(RESULTS_FOLDER)
        if not results_path.exists():
            return
        now = time.time()
        for entry in results_path.iterdir():
            if entry.is_dir() and (now - entry.stat().st_mtime) > MAX_RESULTS_AGE_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("Cleaned up old results: %s", entry.name)
    except Exception:
        logger.exception("Error during results cleanup")


# ---------------------------------------------------------------------------
# Prompt & parsing
# ---------------------------------------------------------------------------
def _build_grading_prompt(filename: str, rubric_text: str) -> str:
    """Create a deterministic prompt requiring a numeric total score and a clean CSV row."""
    return f"""
You are grading a single student's PDF submission named: {filename}.
STRICT REQUIREMENTS:
- Compute a NUMERIC total score on a 0-100 scale.
  * If the rubric has its own max points, first score on that scale, then convert to 0-100 (round to nearest integer).
  * Do NOT output words like N/A, None, or textual values for the score. Output digits only.
- Produce a short student-facing feedback paragraph mentioning key strengths and deductions.
- Produce EXACTLY ONE CSV row with columns: filename,total_score,comments
  * filename must equal the submitted filename exactly: {filename}
  * total_score must be an integer 0-100
  * comments must be comma-safe; replace any commas with semicolons.

OUTPUT FORMAT (exactly):
---FEEDBACK---
<one short paragraph>
---CSV---
filename,total_score,comments
{filename},<0-100 integer>,<comments without commas>

Example (FORMAT ONLY):
---FEEDBACK---
Clear structure and correct method; minor notation issues. Missed edge case in Q3 (-5).
---CSV---
filename,total_score,comments
example.pdf,92,Strong solution; minor notation; missed edge case in Q3 (-5)

RUBRIC:
{rubric_text}
""".strip()


def _extract_feedback_and_csv(text: str, fallback_filename: str):
    """Extract the feedback paragraph and one CSV row from model output."""
    feedback = ""
    csv_row = ""

    if "---CSV---" in text:
        parts = text.split("---CSV---", 1)
        before, after = parts[0], parts[1]
        if "---FEEDBACK---" in before:
            feedback = before.split("---FEEDBACK---", 1)[1].strip()
        else:
            feedback = before.strip()
        for line in after.splitlines():
            if line.strip() and line.count(",") >= 2:
                csv_row = line.strip()
                break
    else:
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        feedback = paras[0] if paras else text.strip()
        for line in text.splitlines():
            if line.strip() and line.count(",") >= 2:
                csv_row = line.strip()
                break

    if not csv_row:
        safe_feedback = re.sub(r",", ";", feedback)
        csv_row = f"{fallback_filename},,{safe_feedback[:500]}"

    # Enforce numeric total_score and sanitize comments
    try:
        parts = [p.strip() for p in csv_row.split(",")]
        while len(parts) < 3:
            parts.append("")
        fname, score_str, comments = parts[0], parts[1], ",".join(parts[2:])

        def parse_score_from_text(txt: str):
            m = re.search(r"(?i)(total|score)\s*[:=]\s*(\d{1,3})", txt)
            if m:
                return int(m.group(2))
            m = re.search(r"(?i)(\d{1,3})\s*/\s*100", txt)
            if m:
                return int(m.group(1))
            m = re.search(r"(?i)(\d{1,3})\s*(?:points|pt)?\s*(?:out of|/)?\s*100", txt)
            if m:
                return int(m.group(1))
            return None

        score_val = None
        if score_str.isdigit():
            score_val = int(score_str)
        else:
            score_val = parse_score_from_text(text) or parse_score_from_text(feedback)

        if score_val is None:
            score_val = 0
            comments = (comments + "; score not provided").strip("; ")
        score_val = max(0, min(100, score_val))

        comments = re.sub(r",", ";", comments)
        csv_row = f"{fname or fallback_filename},{score_val},{comments}".strip()
    except Exception:
        if not csv_row.startswith(fallback_filename):
            csv_row = f"{fallback_filename},{csv_row}"

    return feedback, csv_row


# ---------------------------------------------------------------------------
# Grading pipeline
# ---------------------------------------------------------------------------
def _grade_single_pdf(client, pdf_path, rubric_text):
    """Grade a single PDF file. Returns a result dict."""
    filename = os.path.basename(pdf_path)
    try:
        file_size = os.path.getsize(pdf_path)
        if file_size > MAX_FILE_SIZE:
            return {"filename": filename, "error": f"File too large ({file_size // (1024 * 1024)}MB)"}

        logger.info("Grading file: %s", filename)
        with open(pdf_path, "rb") as f:
            pdf_data = f.read()

        prompt = _build_grading_prompt(filename, rubric_text)

        # Retry with exponential backoff for transient failures
        response = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        types.Part.from_bytes(data=pdf_data, mime_type="application/pdf"),
                        prompt,
                    ],
                )
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    logger.warning("Retry %d for %s: %s", attempt + 1, filename, e)
                    time.sleep(2 ** attempt)
                else:
                    raise

        text = response.text if hasattr(response, "text") else (
            response.candidates[0].content.parts[0].text
            if getattr(response, "candidates", None)
            else ""
        )

        feedback, csv_row = _extract_feedback_and_csv(text or "", filename)
        return {"filename": filename, "feedback": feedback, "csv_row": csv_row}
    except Exception as e:
        logger.error("Error grading %s: %s", filename, e)
        return {"filename": filename, "error": str(e)}


def grade_submissions(client, pdf_files, rubric_text):
    """Grade PDF submissions using Gemini API with parallel execution."""
    if len(pdf_files) == 1:
        return [_grade_single_pdf(client, pdf_files[0], rubric_text)]

    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pdf_files))) as executor:
        future_to_path = {
            executor.submit(_grade_single_pdf, client, path, rubric_text): path
            for path in pdf_files
        }
        for future in as_completed(future_to_path):
            results.append(future.result())

    results.sort(key=lambda r: r["filename"])
    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def create_grading_report_pdf(results, output_path):
    """Create a PDF report from grading feedback."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)

    for i, item in enumerate(results):
        pdf.add_page()
        if "error" in item:
            text = f"Error grading {item['filename']}: {item['error']}"
        else:
            text = item.get("feedback", "")
        pdf.multi_cell(0, 10, f"Grading Report {i + 1} - {item['filename']}\n\n{text}")

    pdf.output(output_path)


def write_csv_report(results, csv_output_path):
    """Write a CSV report from collected csv_row values."""
    header = "filename,total_score,comments"
    lines = [header]
    for item in results:
        if "error" in item:
            lines.append(f"{item['filename']},,Error: {item['error'].replace(',', ';')}")
        else:
            row = item.get("csv_row", "").strip()
            if row.lower().startswith("filename,total_score,comments"):
                parts = row.splitlines()
                row = parts[1].strip() if len(parts) > 1 else ""
            if row:
                lines.append(row)
            else:
                lines.append(f"{item['filename']},,")

    with open(csv_output_path, "w", newline="", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip() + "\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health_check():
    """Health check for load balancers and monitoring."""
    status = {"status": "healthy", "version": "1.0.0"}
    try:
        get_gemini_client()
        status["gemini"] = "connected"
    except Exception:
        status["gemini"] = "unavailable"
        status["status"] = "degraded"
    code = 200 if status["status"] == "healthy" else 503
    return jsonify(status), code


@app.route("/api/grade", methods=["POST"])
@limiter.limit("10 per hour")
def grade_assignments():
    try:
        _cleanup_old_results()

        # Validate inputs
        if "rubric" not in request.form:
            return jsonify({"error": "Rubric text is required"}), 400
        if "submissions" not in request.files:
            return jsonify({"error": "No submissions file uploaded"}), 400

        rubric_text = request.form["rubric"].strip()
        if len(rubric_text) < 10:
            return jsonify({"error": "Rubric is too short. Please provide a detailed rubric."}), 400
        if len(rubric_text) > MAX_RUBRIC_LENGTH:
            return jsonify({"error": f"Rubric is too long (max {MAX_RUBRIC_LENGTH:,} characters)."}), 400

        submissions_file = request.files["submissions"]
        if submissions_file.filename == "":
            return jsonify({"error": "No file selected"}), 400
        if not allowed_file(submissions_file.filename):
            return jsonify({"error": "Invalid file type. Please upload PDF or ZIP files only."}), 400

        # Initialize Gemini client
        try:
            client = get_gemini_client()
        except Exception as e:
            return jsonify({"error": f"Failed to initialize AI client: {str(e)}"}), 500

        # Save uploaded file
        filename = secure_filename(submissions_file.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        submissions_file.save(file_path)

        # Process submissions
        pdf_files = []
        if filename.lower().endswith(".zip"):
            extract_dir = os.path.join(app.config["UPLOAD_FOLDER"], "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            pdf_files = extract_pdf_files(file_path, extract_dir)
        else:
            pdf_files = [file_path]

        if not pdf_files:
            return jsonify({"error": "No PDF files found in the uploaded content"}), 400

        # Grade submissions (parallel for multiple files)
        results = grade_submissions(client, pdf_files, rubric_text)

        # Create results directory
        session_id = uuid.uuid4().hex[:12]
        session_results_dir = os.path.join(app.config["RESULTS_FOLDER"], session_id)
        os.makedirs(session_results_dir, exist_ok=True)

        # Generate reports
        pdf_report_path = os.path.join(session_results_dir, "grading_reports.pdf")
        create_grading_report_pdf(results, pdf_report_path)

        csv_report_path = os.path.join(session_results_dir, "grading_report.csv")
        write_csv_report(results, csv_report_path)

        # Clean up uploaded files
        os.remove(file_path)
        if filename.lower().endswith(".zip"):
            shutil.rmtree(
                os.path.join(app.config["UPLOAD_FOLDER"], "extracted"),
                ignore_errors=True,
            )

        return jsonify({
            "success": True,
            "session_id": session_id,
            "graded_count": len([r for r in results if "error" not in r]),
            "error_count": len([r for r in results if "error" in r]),
            "pdf_report_url": f"/api/download/{session_id}/pdf",
            "csv_report_url": f"/api/download/{session_id}/csv",
        })

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Unexpected error during grading")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.route("/api/download/<session_id>/<file_type>")
def download_report(session_id, file_type):
    # Prevent path traversal
    if not re.match(r"^[a-f0-9]{12}$", session_id):
        return jsonify({"error": "Invalid session ID"}), 400

    try:
        session_results_dir = os.path.join(app.config["RESULTS_FOLDER"], session_id)

        if file_type == "pdf":
            file_path = os.path.join(session_results_dir, "grading_reports.pdf")
            return send_file(file_path, as_attachment=True, download_name="grading_reports.pdf")
        elif file_type == "csv":
            file_path = os.path.join(session_results_dir, "grading_report.csv")
            return send_file(file_path, as_attachment=True, download_name="grading_report.csv")
        else:
            return jsonify({"error": "Invalid file type"}), 400

    except Exception as e:
        return jsonify({"error": f"File not found: {str(e)}"}), 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug, host="0.0.0.0", port=port)
