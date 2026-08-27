from django.db import models

# Create your models here.
class Job(models.Model):
    # so we have to updat this for two file uploads
    # i think, i need the bank_file a ledge_file and their respective filenames
    bank_file=models.FileField(upload_to='uploads/')
    bank_filename=models.CharField(max_length=255)
    ledger_file=models.FileField(upload_to='uploads/')
    ledger_filename=models.CharField(max_length=255)

    # date at which created and the process is completed at definetely
    created_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True, blank=True)

    # error is an important field since we can't return errors
    error=models.CharField(null=True, blank=True)
    status=models.CharField(max_length=40)

    # result okay? this one will be only can be known afterwards 
    # after the complete pipelines has been changed...
    results=models.JSONField(null=True, blank=True)
    # row_count_raw=models.IntegerField(default=0)
    # row_count_clean=models.IntegerField(default=0)
