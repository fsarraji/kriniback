import csv
import os
from django.core.management.base import BaseCommand
from fleet.models import Brand, ModelCar

class Command(BaseCommand):
    help = 'Import car brands and models from base1_modeles.csv'

    def handle(self, *args, **options):
        csv_path = os.path.join(os.getcwd(), 'base1_modeles.csv')
        
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.ERROR(f'File not found: {csv_path}'))
            return

        with open(csv_path, mode='r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            
            brands_count = 0
            models_count = 0
            
            for row in reader:
                brand_name = row['rappel_marque'].strip().upper()
                model_name = row['modele'].strip()
                
                # Get or create brand
                brand, created = Brand.objects.get_or_create(name=brand_name)
                if created:
                    brands_count += 1
                
                # Get or create model
                model, created = ModelCar.objects.get_or_create(
                    brand=brand,
                    name=model_name
                )
                if created:
                    models_count += 1
            
            self.stdout.write(self.style.SUCCESS(f'Successfully imported {brands_count} new brands and {models_count} new models.'))
