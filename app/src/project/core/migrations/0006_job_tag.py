# Generated by Django 4.2.11 on 2024-04-20 20:50

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_alter_job_input_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="tag",
            field=models.CharField(
                blank=True,
                default="",
                help_text="may be used to group jobs",
                max_length=255,
            ),
        ),
    ]