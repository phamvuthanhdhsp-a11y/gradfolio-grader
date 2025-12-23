# Gradfolio Rubric Grader (FastAPI + SQLite) — Replit-ready

## Tính năng
- Upload PDF -> Chấm điểm theo Rubric Active (hoặc chọn version)
- Trích xuất nội dung PDF:
  - Text layer: PyMuPDF (chính) + fallback pdfplumber
  - OCR: pytesseract (chỉ OCR những trang ít text) nếu hệ thống có `tesseract`
  - Cache kết quả trích xuất theo file_hash trong SQLite để chấm lại nhanh
- Rubric Manager:
  - Tạo rubric mới
  - Versioning: edit JSON và lưu thành version mới
  - Set Active, Export/Import JSON
- History: lưu lịch sử chấm + metadata
- Output chuẩn JSON theo schema yêu cầu, có download report.json / report.html
- LLM chấm điểm (optional):
  - Nếu có `OPENAI_API_KEY` trong env -> gọi OpenAI trả về STRICT JSON
  - Nếu không có -> fallback heuristic (keyword/sections/length)

## Cách chạy trên Replit (Python Repl)
1) Tạo Repl mới: **Python**
2) Tạo folder/file đúng cây thư mục trong repo này, dán code tương ứng.
3) Cài dependencies: Replit sẽ tự cài từ `requirements.txt` khi bạn Run.

### Run command gợi ý (Replit)
- Trong Replit, đặt “Run command” là:
  `uvicorn backend.main:app --host 0.0.0.0 --port 3000`

App sẽ chạy ở Webview của Replit.

## OPENAI_API_KEY (optional)
- Nếu muốn chấm bằng AI/LLM:
  - Vào Replit -> Secrets/Environment -> thêm `OPENAI_API_KEY`
  - (Optional) `OPENAI_MODEL` (default: gpt-4o-mini)

Nếu không có key, app vẫn chạy (heuristic).

## OCR (Tesseract)
- App chỉ bật OCR nếu hệ thống có `tesseract`.
- Nếu Replit environment không có Tesseract:
  - OCR tự tắt, UI sẽ hiển thị cảnh báo “OCR: TẮT”
  - App vẫn chấm dựa trên text layer.

## API Docs
- Swagger: `/docs`

## Seed rubric mặc định
- Lần chạy đầu tiên, app sẽ seed rubric từ `config/rubric.default.json` vào SQLite và set Active.
- Database: `data/app.db`
- Uploads: `uploads/`
