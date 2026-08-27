from celery import shared_task

from .models import Job
# from .services import TransactionProcessor
from .services import ReconciliationEngine

@shared_task(bind=True, autoretry_for=(Exception,),retry_backoff=True,retry_kwargs={"max_retries":3})
def process_job(self, job_id):
    job = Job.objects.get(id=job_id)

    try:
        job.status = "processing"
        job.save()

        # processor = TransactionProcessor()
        processor = ReconciliationEngine()

        results = processor.process(job.bank_file, job.ledger_file)

        job.results = results

        job.status = "finished"

    except Exception as e:
        job.status = "failed"
        job.error = str(e)

    finally:
        job.save()