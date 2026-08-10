from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0011_vehicle_gps_imei_vehicle_sim_number_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicle',
            name='is_archived',
            field=models.BooleanField(default=False, verbose_name='Archivé'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='date_fin_travail',
            field=models.DateField(blank=True, null=True, verbose_name='Date de fin de travail'),
        ),
    ]
