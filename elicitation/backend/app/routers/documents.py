"""Routes for markdown documents (canonical + user)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.markdown_generator import generate_process_markdown, generate_project_markdown
from app.models import Document
from app.schemas import DocumentCreate, DocumentOut, DocumentUpdate

router = APIRouter(tags=["documents"])


# ── Project documents ─────────────────────────────────────────────────────

@router.post("/projects/{project_id}/documents", response_model=DocumentOut, status_code=201)
async def create_document(project_id: str, data: DocumentCreate, db: AsyncSession = Depends(get_db)):
    doc = Document(
        project_id=project_id,
        process_id=data.process_id,
        title=data.title,
        content=data.content,
        doc_type=data.doc_type,
        is_canonical=False,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get("/projects/{project_id}/documents", response_model=list[DocumentOut])
async def list_project_documents(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Document)
        .where(Document.project_id == project_id, Document.process_id.is_(None))
        .order_by(Document.is_canonical.desc(), Document.created_at.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ── Process documents ─────────────────────────────────────────────────────

@router.get("/processes/{process_id}/documents", response_model=list[DocumentOut])
async def list_process_documents(
    process_id: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Document)
        .where(Document.process_id == process_id)
        .order_by(Document.is_canonical.desc(), Document.created_at.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ── Single document CRUD ──────────────────────────────────────────────────

@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.put("/documents/{document_id}", response_model=DocumentOut)
async def update_document(document_id: str, data: DocumentUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if data.title is not None:
        doc.title = data.title
    if data.content is not None:
        doc.content = data.content
    await db.commit()
    await db.refresh(doc)
    return doc


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.is_canonical:
        raise HTTPException(400, "Cannot delete canonical documents — regenerate instead")
    await db.delete(doc)
    await db.commit()


# ── Generate canonical documents ──────────────────────────────────────────

@router.post("/projects/{project_id}/documents/generate", response_model=DocumentOut)
async def generate_project_doc(project_id: str, db: AsyncSession = Depends(get_db)):
    content = await generate_project_markdown(db, project_id)

    # Find existing canonical doc for this project (no process)
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.process_id.is_(None),
            Document.is_canonical.is_(True),
        )
    )
    doc = result.scalar_one_or_none()

    if doc:
        doc.content = content
    else:
        doc = Document(
            project_id=project_id,
            process_id=None,
            title="Project Documentation",
            content=content,
            doc_type="canonical",
            is_canonical=True,
        )
        db.add(doc)

    await db.commit()
    await db.refresh(doc)
    return doc


@router.post("/processes/{process_id}/documents/generate", response_model=DocumentOut)
async def generate_process_doc(process_id: str, db: AsyncSession = Depends(get_db)):
    content = await generate_process_markdown(db, process_id)

    # We need the project_id from the process
    from app.models import Process
    result = await db.execute(select(Process).where(Process.id == process_id))
    process = result.scalar_one_or_none()
    if not process:
        raise HTTPException(404, "Process not found")

    # Find existing canonical doc for this process
    result = await db.execute(
        select(Document).where(
            Document.process_id == process_id,
            Document.is_canonical.is_(True),
        )
    )
    doc = result.scalar_one_or_none()

    if doc:
        doc.content = content
    else:
        doc = Document(
            project_id=process.project_id,
            process_id=process_id,
            title=f"Process: {process.name}",
            content=content,
            doc_type="canonical",
            is_canonical=True,
        )
        db.add(doc)

    await db.commit()
    await db.refresh(doc)
    return doc
