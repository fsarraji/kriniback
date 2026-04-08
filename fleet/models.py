from django.db import models
from agency.models import Agency


class Brand(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="Nom de la marque")
    
    def __str__(self):
        return self.name

class ModelCar(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name='models', verbose_name="Marque")
    name = models.CharField(max_length=100, verbose_name="Nom du modèle")
    
    class Meta:
        unique_together = ('brand', 'name')
        verbose_name = "Modèle de voiture"
        verbose_name_plural = "Modèles de voitures"

    def __str__(self):
        return f"{self.brand.name} {self.name}"


class Vehicle(models.Model):
    FUEL_CHOICES =[
        ('Diesel', 'Diesel'),
        ('Essence', 'Essence'),
        ('Hybride', 'Hybride'),
        ('Electrique', 'Electrique'),
    ]
    
    STATUS_CHOICES =[
        ('Available', 'Disponible'),
        ('Rented', 'Loué'),
        ('Maintenance', 'En maintenance'),
    ]
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='vehicles')
    matricule = models.CharField(max_length=20, unique=True, verbose_name="Matricule")
    marque = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, verbose_name="Marque") 
    modele = models.ForeignKey(ModelCar, on_delete=models.SET_NULL, null=True, verbose_name="Modèle")
    annee = models.IntegerField(verbose_name="Année")
    couleur = models.CharField(max_length=30, verbose_name="Couleur")
    carburant = models.CharField(max_length=20, choices=FUEL_CHOICES, verbose_name="Carburant")
    kilometrage = models.IntegerField(verbose_name="Kilométrage")
    prix_par_jour = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Prix par jour")
    chauffeur_disponible = models.BooleanField(default=False, verbose_name="Chauffeur disponible")
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Available', verbose_name="Statut")
    
    date_assurance = models.DateField(verbose_name="Date assurance")
    date_visite_technique = models.DateField(verbose_name="Date visite technique")
    prochain_vidange_km = models.IntegerField(verbose_name="Prochain vidange (km)")
    
    image = models.ImageField(upload_to='vehicles_images/', null=True, blank=True)

    def __str__(self):
        return f"{self.marque} {self.modele} - {self.matricule}"