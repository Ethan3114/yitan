import asyncio
from sqlalchemy import update

from app.models import Call, DraftTask, WritingRun, utcnow
from app.services.draft import build_draft
from app.services.tingwu import ensure_full_transcript
from app.services.analytics import server_event


def task_view(task: DraftTask) -> dict:
    return {
        "id": task.id, "call_id": task.call_id, "status": task.status,
        "step": task.step, "error_code": task.error_code,
        "created_at": task.created_at.isoformat(), "updated_at": task.updated_at.isoformat(),
    }


def recover_interrupted(factory) -> None:
    with factory() as db:
        db.execute(update(DraftTask).where(DraftTask.status.in_(["queued", "running"])).values(
            status="failed", error_code="service_restarted", updated_at=utcnow()))
        db.execute(update(WritingRun).where(WritingRun.status == "running").values(
            status="interrupted", error_code="service_restarted"))
        db.commit()


async def run_task(app, task_id: str) -> None:
    factory = app.state.db_factory
    with factory() as db:
        task = db.get(DraftTask, task_id)
        task.status = "running"
        task.step = "transcribing"
        task.updated_at = utcnow()
        db.commit()
        owner, call_id = task.owner_id, task.call_id

    def transcribe():
        with factory() as db:
            ensure_full_transcript(db, app.state.settings, app.state.fernet,
                                   app.state.settings.data_path, owner, call_id)

    await asyncio.to_thread(transcribe)
    with factory() as db:
        task = db.get(DraftTask, task_id)
        task.step = "writing"
        server_event(db, "transcription_completed", task.owner_id, task.call_id)
        task.updated_at = utcnow()
        db.commit()
        call = db.get(Call, call_id)
        await build_draft(db, app.state.settings, owner, call_id, call.profile_id)
        task.status = "succeeded"
        task.step = "done"
        server_event(db, "memoir_completed", task.owner_id, task.call_id)
        task.updated_at = utcnow()
        db.commit()


def launch_task(app, task_id: str) -> None:
    work = asyncio.create_task(run_task(app, task_id))
    app.state.draft_workers.add(work)

    def finished(result):
        app.state.draft_workers.discard(result)
        failure = None if result.cancelled() else result.exception()
        if result.cancelled() or failure is not None:
            with app.state.db_factory() as db:
                task = db.get(DraftTask, task_id)
                task.status = "failed"
                task.error_code = getattr(failure, "code", "draft_failed")
                server_event(db, "operation_failed", task.owner_id, task.call_id, error=task.error_code, stage=task.step)
                task.updated_at = utcnow()
                db.commit()
    work.add_done_callback(finished)
