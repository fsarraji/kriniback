import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from weasyprint import HTML

app = FastAPI(title="Krini PDF Service")


class PDFRequest(BaseModel):
    html: str


class PDFRequestWithOptions(BaseModel):
    html: str


@app.post("/convert")
async def convert(request: PDFRequest):
    try:
        pdf_bytes = HTML(string=request.html).write_pdf()
        return Response(content=pdf_bytes, media_type="application/pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
