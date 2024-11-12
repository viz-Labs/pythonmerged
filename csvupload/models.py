from django.db import models

class CSVData(models.Model):
    data_field = models.CharField(max_length=255)

    def __str__(self):
        return self.data_field