# Generated by Django 4.2.13 on 2024-07-29 08:25

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_jobstartedreceipt_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="jobfinishedreceipt",
            name="job_uuid",
            field=models.UUIDField(db_index=True),
        ),
        migrations.AlterField(
            model_name="jobfinishedreceipt",
            name="miner_hotkey",
            field=models.CharField(db_index=True, max_length=256),
        ),
        migrations.AlterField(
            model_name="jobfinishedreceipt",
            name="time_started",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name="jobfinishedreceipt",
            name="validator_hotkey",
            field=models.CharField(db_index=True, max_length=256),
        ),
        migrations.AlterField(
            model_name="jobstartedreceipt",
            name="job_uuid",
            field=models.UUIDField(db_index=True),
        ),
        migrations.AlterField(
            model_name="jobstartedreceipt",
            name="miner_hotkey",
            field=models.CharField(db_index=True, max_length=256),
        ),
        migrations.AlterField(
            model_name="jobstartedreceipt",
            name="time_accepted",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name="jobstartedreceipt",
            name="validator_hotkey",
            field=models.CharField(db_index=True, max_length=256),
        ),
    ]