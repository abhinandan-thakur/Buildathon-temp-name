from rest_framework import serializers
from .models import Job

class JobSerializer(serializers.ModelSerializer):
    class Meta:
        # ? what does model field represent? shit?
        # * the serializer creates a row of JOB model but takes input only form fields
        model=Job
        # ? so we do expect a file just one?
        fields=["bank_file", "ledger_file"]

class JobListSerializer(serializers.ModelSerializer):
    class Meta:
        model=Job
        fields=['id','status','bank_filename','ledger_filename','created_at','row_count_bank','row_count_ledger','error']