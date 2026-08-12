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

    @property
    def traccar_device_id(self):
        """ID du dispositif Traccar lié (lu depuis la table GpsDevice, relation 1:1)."""
        try:
            device = self.gps_device
        except (GpsDevice.DoesNotExist, AttributeError):
            device = None
        return device.traccar_device_id if device else None


class GpsDevice(models.Model):
    """Dispositif GPS ajouté au serveur Traccar (relation 1:1 avec un véhicule).

    Le serveur Traccar reste la source de vérité (positions, statut, ...) ; cette
    table conserve le lien dispositif ↔ véhicule de l'agence ainsi qu'une copie
    des informations de base du dispositif (nom, IMEI, statut).
    """

    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='gps_devices', verbose_name="Agence")
    vehicle = models.OneToOneField(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='gps_device',
        verbose_name="Véhicule",
    )
    traccar_device_id = models.BigIntegerField(verbose_name="ID dispositif Traccar")
    name = models.CharField(max_length=200, blank=True, default='', verbose_name="Nom du dispositif")
    unique_id = models.CharField(max_length=100, blank=True, default='', verbose_name="ID / IMEI du dispositif GPS")
    status = models.CharField(max_length=20, blank=True, default='', verbose_name="Statut (online/offline)")
    last_update = models.DateTimeField(null=True, blank=True, verbose_name="Dernière mise à jour")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('agency', 'traccar_device_id')
        ordering = ['-created_at']
        verbose_name = "Dispositif GPS"
        verbose_name_plural = "Dispositifs GPS"

    def __str__(self):
        return f"{self.name or 'Dispositif'} #{self.traccar_device_id}"

    @classmethod
    def attach(cls, vehicle, device_id, agency=None, name=None, unique_id=None):
        """Attribue le dispositif Traccar `device_id` au véhicule (relation 1:1).

        Délie ce dispositif de tout autre véhicule et délie ce véhicule de tout
        autre dispositif, puis enregistre le lien. Retourne le GpsDevice.
        """
        agency = agency or vehicle.agency
        cls.objects.filter(traccar_device_id=device_id, agency=agency).exclude(vehicle=vehicle).update(vehicle=None)
        cls.objects.filter(vehicle=vehicle).exclude(traccar_device_id=device_id).update(vehicle=None)
        device, _ = cls.objects.get_or_create(
            agency=agency,
            traccar_device_id=device_id,
            defaults={'vehicle': vehicle, 'name': name or '', 'unique_id': unique_id or ''},
        )
        updates = []
        if device.vehicle_id != vehicle.pk:
            device.vehicle = vehicle
            updates.append('vehicle')
        if name is not None and (device.name or '') != name:
            device.name = name
            updates.append('name')
        if unique_id is not None and (device.unique_id or '') != unique_id:
            device.unique_id = unique_id
            updates.append('unique_id')
        if updates:
            device.save(update_fields=updates)
        return device

    @classmethod
    def detach(cls, vehicle):
        """Délie le véhicule de son dispositif (il n'y en a qu'un, relation 1:1)."""
        cls.objects.filter(vehicle=vehicle).update(vehicle=None)

    @classmethod
    def detach_device(cls, device_id, agency=None):
        """Délie le dispositif du véhicule auquel il est éventuellement affecté."""
        qs = cls.objects.filter(traccar_device_id=device_id)
        if agency is not None:
            qs = qs.filter(agency=agency)
        qs.update(vehicle=None)


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