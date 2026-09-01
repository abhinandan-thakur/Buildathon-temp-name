from django.shortcuts import render, get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.core.cache import cache

from .models import Job
from .serializers import JobSerializer, JobListSerializer

from .tasks import process_job

import time

import logging

logger = logging.getLogger(__name__)

class JobView(APIView):
    def post(self, request):
        # ? what does this do a counter of the starting time? why?
        # start = time.perf_counter()

        # ? serializer does it serialize the input request json into a serializer pyobject?
        # * JobSerializer describes what data you're expecting and how it should be validated.
        serializer = JobSerializer(data=request.data)
        # * This actually validates it.
        serializer.is_valid(raise_exception=True)

        # ? why are we storing another time here? no idea...
        # validation_end = time.perf_counter()

        # ? uploadaed file? meaning? what does this code do?
        bankFile = serializer.validated_data["bank_file"]
        # bankFileName = serializer.validated_data["bank_filename"]
        ledgerFile = serializer.validated_data["ledger_file"]
        # ledgerFileName = serializer.validated_data["ledger_filename"]

        # ? does this create a job for async file upload?  I am so confusd... 
        # * This is where you actually create a database record.
        job = Job.objects.create(
            bank_file=bankFile,
            bank_filename=bankFile.name,
            ledger_file=ledgerFile,
            ledger_filename=ledgerFile.name,
            status="pending"
        )

        # Anothr time feeling like some left up cleanup job this now...
        # db_end = time.perf_counter()

        # * this hands off the work to CELERY 
        # COMMENT THIS TEMP
        # we need to check if the endpoint is working right or not
        # TODO we need to make this celery worker work
        process_job.delay(job.id)

        # ;(
        # celery_end = time.perf_counter()

        return Response({"job_id": job.id, "status": "pending"}, status=202)


# ! GO into depth on this one this can be imporved according to me i have done that weird caching before
class JobListView(APIView):
    def get(self, request):
        version = cache.get("job_list_version", 1)
        status_filter = request.query_params.get("status", "")
        page = request.query_params.get("page", "1")
        page_size = request.query_params.get("page_size", "20")
        cacheKey = f"job_list:v{version}:{status_filter}:{page}:{page_size}"

        cacheResponse = cache.get(cacheKey)
        if cacheResponse is not None:
            return Response(cacheResponse)

        queryset = Job.objects.all()
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        page_num = int(page)
        page_size_num = int(page_size)
        start = (page_num - 1) * page_size_num
        end = start + page_size_num

        total = queryset.count()
        jobs = queryset.values(
            "id", "status", "bank_filename", "ledger_filename",
            "row_count_bank", "row_count_ledger", "match_rate",
            "created_at", "error",
        )[start:end]

        data = {
            "count": total,
            "page": page_num,
            "page_size": page_size_num,
            "results": list(jobs),
        }
        cache.set(cacheKey, data, timeout=5)  # short TTL — list changes as jobs progress
        return Response(data)

class JobStatusView(APIView):
    def get(self, request, job_id):
        cacheKey = f"job_status:{job_id}"
        cacheResponse = cache.get(cacheKey)
        if cacheResponse is not None:
            return Response(cacheResponse, 200)
        job = get_object_or_404(Job, id=job_id )
        data = {"id": job.id, "status": job.status}
        cache.set(cacheKey, data, timeout=300)
        return Response(data,200)

class JobResultView(APIView):
    def get(self, request, job_id):
        cacheKey = f"job_result:{job_id}"
        cacheResponse = cache.get(cacheKey)
        if cacheResponse is not None:
            return Response(cacheResponse, 200)
        job = get_object_or_404(Job, id=job_id)
        data = {"id": job.id, "result": job.results, "error": job.error}
        if job.status in ("finished", "failed"):
            cache.set(cacheKey, data, timeout=None)
        return Response(data,200)