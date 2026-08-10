from django.db import models
from agency.models import Agency
from car_rental_backend.uploads import vehicle_image_upload_to


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
    
    image = models.ImageField(upload_to=vehicle_image_upload_to, null=True, blank=True)
    tarif_km_extra = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name="Tarif km supplémentaire (DH/km)")
    traccar_device_id = models.BigIntegerField(null=True, blank=True, verbose_name="ID dispositif Traccar")

    # Dispositif GPS installé dans le véhicule
    gps_imei = models.CharField(max_length=50, null=True, blank=True, verbose_name="ID / IMEI du dispositif GPS")
    sim_number = models.CharField(max_length=30, null=True, blank=True, verbose_name="Numéro carte SIM")
    sim_operator = models.CharField(max_length=50, null=True, blank=True, verbose_name="Opérateur télécom")

    # Archivage (fin de travail)
    is_archived = models.BooleanField(default=False, verbose_name="Archivé")
    date_fin_travail = models.DateField(null=True, blank=True, verbose_name="Date de fin de travail")

    # Suppression douce (masqué de la flotte, restauré par le super admin)
    is_deleted = models.BooleanField(default=False, verbose_name="Supprimé")

    @property
    def km_loue_total(self):
        """Nombre total de kilomètres parcourus par les locataires (contrats terminés)."""
        from django.db.models import F, Sum
        result = self.contracts.filter(
            statut='TERMINE',
            km_retour__isnull=False,
            km_retour__gte=F('km_sortie'),
        ).aggregate(total=Sum(F('km_retour') - F('km_sortie')))
        return result.get('total') or 0

    def __str__(self):
        return f"{self.marque} {self.modele} - {self.matricule}"


class Evaluation(models.Model):
    """Évaluation (note + commentaire) d'un véhicule par un client connecté."""

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='evaluations')
    client = models.ForeignKey('clients.Client', on_delete=models.CASCADE, related_name='evaluations')
    rating = models.PositiveSmallIntegerField(verbose_name="Note (1 à 5)")
    comment = models.TextField(blank=True, null=True, verbose_name="Commentaire")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('vehicle', 'client')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.client} - {self.vehicle} ({self.rating}/5)"