from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0009_alter_pdfjob_pdf_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='pdfjob',
            name='template',
            field=models.CharField(choices=[('standard', 'Standard'), ('minimal', 'Minimal')], default='standard', max_length=20, verbose_name='Template contrat'),
        ),
    ]