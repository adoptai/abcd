"""Routes for questions (clarification tracking)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Project, Question
from app.schemas import QuestionCreate, QuestionOut, QuestionUpdate
from app.timeline_utils import emit_timeline_event

router = APIRouter(tags=["questions"])


@router.post("/projects/{project_id}/questions", response_model=QuestionOut, status_code=201)
async def create_question(
    project_id: str,
    data: QuestionCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Project not found")

    question = Question(
        project_id=project_id,
        process_id=data.process_id,
        content=data.content,
        context_url=data.context_url,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)

    try:
        await emit_timeline_event(
            db,
            project_id=project_id,
            process_id=data.process_id,
            event_type="question",
            source_id=question.id,
            summary=f"Question: {data.content[:80]}",
        )
        await db.commit()
    except Exception:
        pass

    return question


@router.get("/projects/{project_id}/questions", response_model=list[QuestionOut])
async def list_questions(
    project_id: str,
    process_id: str | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Question).where(Question.project_id == project_id)
    if process_id:
        stmt = stmt.where(Question.process_id == process_id)
    if status:
        stmt = stmt.where(Question.status == status)
    stmt = stmt.order_by(Question.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/questions/{question_id}", response_model=QuestionOut)
async def get_question(question_id: str, db: AsyncSession = Depends(get_db)):
    return await _get_question(question_id, db)


@router.put("/questions/{question_id}", response_model=QuestionOut)
async def update_question(
    question_id: str,
    data: QuestionUpdate,
    db: AsyncSession = Depends(get_db),
):
    question = await _get_question(question_id, db)
    old_status = question.status

    if data.content is not None:
        question.content = data.content
    if data.answer is not None:
        question.answer = data.answer
    if data.status is not None:
        question.status = data.status
    if data.context_url is not None:
        question.context_url = data.context_url

    await db.commit()
    await db.refresh(question)

    # Emit timeline event on status change
    if data.status and data.status != old_status:
        try:
            event_type = f"question_{data.status}"
            summary_prefix = {"answered": "Answered", "resolved": "Resolved", "open": "Reopened"}.get(data.status, "Updated")
            await emit_timeline_event(
                db,
                project_id=question.project_id,
                process_id=question.process_id,
                event_type=event_type,
                source_id=question.id,
                summary=f"{summary_prefix}: {question.content[:80]}",
            )
            await db.commit()
        except Exception:
            pass

    return question


@router.delete("/questions/{question_id}", status_code=204)
async def delete_question(question_id: str, db: AsyncSession = Depends(get_db)):
    question = await _get_question(question_id, db)
    await db.delete(question)
    await db.commit()


async def _get_question(question_id: str, db: AsyncSession) -> Question:
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(404, "Question not found")
    return question
