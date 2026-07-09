"""CRUD routes for processes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Process, Project
from app.schemas import ProcessCreate, ProcessOut, ProcessUpdate

router = APIRouter(tags=["processes"])


@router.post("/projects/{project_id}/processes", response_model=ProcessOut, status_code=201)
async def create_process(project_id: str, body: ProcessCreate, db: AsyncSession = Depends(get_db)):
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Project not found")
    process = Process(project_id=project_id, name=body.name, description=body.description, base_url=body.base_url)
    db.add(process)
    await db.commit()
    await db.refresh(process)
    return process


@router.get("/projects/{project_id}/processes", response_model=list[ProcessOut])
async def list_processes(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Process).where(Process.project_id == project_id).order_by(Process.created_at.desc())
    )
    return result.scalars().all()


@router.get("/processes/{process_id}", response_model=ProcessOut)
async def get_process(process_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Process).where(Process.id == process_id))
    process = result.scalar_one_or_none()
    if not process:
        raise HTTPException(404, "Process not found")
    return process


@router.put("/processes/{process_id}", response_model=ProcessOut)
async def update_process(process_id: str, body: ProcessUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Process).where(Process.id == process_id))
    process = result.scalar_one_or_none()
    if not process:
        raise HTTPException(404, "Process not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(process, field, value)
    await db.commit()
    await db.refresh(process)
    return process


@router.delete("/processes/{process_id}", status_code=204)
async def delete_process(process_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Process).where(Process.id == process_id))
    process = result.scalar_one_or_none()
    if not process:
        raise HTTPException(404, "Process not found")
    await db.delete(process)
    await db.commit()
