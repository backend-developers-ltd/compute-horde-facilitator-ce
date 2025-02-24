# Generated by Django 4.2.19 on 2025-02-21 14:28

import django.contrib.postgres.fields
from django.db import migrations, models


def delete_jobs(apps, schema_editor):
    Job = apps.get_model("core", "Job")
    Job.objects.all().delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0037_alter_job_volumes"),
    ]

    operations = [
        migrations.RunPython(delete_jobs),
        migrations.AlterField(
            model_name="job",
            name="args",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.TextField(),
                blank=True,
                default=list,
                help_text="arguments passed to the script or docker image",
                null=True,
                size=None,
            ),
        ),
    ]
