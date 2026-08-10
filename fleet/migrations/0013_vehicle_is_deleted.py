from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0012_vehicle_is_archived_date_fin_travail'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicle',
            name='is_deleted',
            field=models.BooleanField(default=False, verbose_name='Supprimé'),
        ),
    ]
