from celery import shared_task

from .models import Job
from django.core.cache import cache
# from .services import TransactionProcessor
from .services import ReconciliationEngine

@shared_task(
    bind=True,
    queue='reconcile',
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def process_job(self, job_id):
    job = Job.objects.get(id=job_id)
    cacheKey = f"job_status:{job_id}"

    try:
        job.status = "processing"
        job.save()
        cache.delete(cacheKey)
        data = {"id": job.id, "status": "processing"}
        cache.set(cacheKey, data, timeout=300)

        # processor = TransactionProcessor()
        processor = ReconciliationEngine()

        results = processor.process(job.bank_file, job.ledger_file)

        job.results = results

        job.status = "finished"
        data = {"id": job.id, "status": "finished"}
        cache.delete(cacheKey)
        cache.set(cacheKey, data, timeout=None)

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        cache.delete(cacheKey)
        data = {"id": job.id, "status": "failed"}
        cache.set(cacheKey, data, timeout=None)

    finally:
        job.save()