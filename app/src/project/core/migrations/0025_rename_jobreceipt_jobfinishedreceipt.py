# Generated by Django 4.2.13 on 2024-07-09 18:41

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_job_uploads_job_volumes"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="JobReceipt",
            new_name="JobFinishedReceipt",
        ),
    ]