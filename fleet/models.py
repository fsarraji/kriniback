import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db import models
from agency.models import Agency
from car_rental_backend.uploads import vehicle_image_upload_to


def _add_months(d, months):
    """Retourne la date `d` décalée de `months` mois (clamp du jour)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _today():
    return date.today()


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
    matricule_definitif = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="Matricule définitif (00000-lettre-00)")
    marque = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, verbose_name="Marque") 
    modele = models.ForeignKey(ModelCar, on_delete=models.SET_NULL, null=True, verbose_name="Modèle")
    annee = models.IntegerField(verbose_name="Année")
    couleur = models.CharField(max_length=30, verbose_name="Couleur")
    carburant = models.CharField(max_length=20, choices=FUEL_CHOICES, verbose_name="Carburant")
    kilometrage = models.IntegerField(verbose_name="Kilométrage")
    prix_par_jour = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Prix par jour (défaut)")
    chauffeur_disponible = models.BooleanField(default=False, verbose_name="Chauffeur disponible")
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Available', verbose_name="Statut")

    date_mise_en_circulation = models.DateField(null=True, blank=True, verbose_name="Date de mise en circulation")
    date_autorisation_circulation = models.DateField(null=True, blank=True, verbose_name="Date d'autorisation de circulation")
    puissance_fiscale = models.PositiveIntegerField(null=True, blank=True, verbose_name="Puissance fiscale (CV)")
    
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

    def save(self, *args, **kwargs):
        # Normalise le statut (insensible à la casse / aux espaces) vers les
        # valeurs canoniques. Évite les valeurs erronées ('AVAILABLE',
        # 'available'...) qui cassent les filtres et les boutons des fronts.
        s = (self.statut or '').strip()
        for choice, _ in self.STATUS_CHOICES:
            if s and s.lower() == choice.lower():
                if self.statut != choice:
                    self.statut = choice
                break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.marque} {self.modele} - {self.matricule}"

    @property
    def matricule_actuel(self):
        """Plaque d'immatriculation effective.

        Un véhicule neuf roule avec sa plaque provisoire (WW + chiffres, champ
        `matricule`) jusqu'à un mois après sa mise en circulation. Une fois la
        plaque définitive (format 00000-lettre-00) saisie manuellement dans
        `matricule_definitif` et ce délai écoulé, c'est elle qui s'affiche.
        """
        if self.matricule_definitif and self.date_mise_en_circulation:
            if _today() >= _add_months(self.date_mise_en_circulation, 1):
                return self.matricule_definitif
        return self.matricule

    def _interval_pour_date(self, jour):
        """Période tarifaire couvrant `jour` (priorité ABSOLUTE > RECURRENT,
        puis prix le plus élevé entre deux périodes du même type)."""
        if not isinstance(jour, date):
            jour = jour.date()
        best, best_rank, best_prix = None, -1, None
        for interval in self.price_intervals.all():
            if not interval.contient(jour):
                continue
            rank = VehiclePriceInterval.TYPE_RANK.get(interval.type, 0)
            if rank > best_rank or (rank == best_rank and (best_prix is None or interval.prix > best_prix)):
                best, best_rank, best_prix = interval, rank, interval.prix
        return best

    def prix_pour_date(self, jour):
        """Prix journalier effectif pour une date (période saisonnière sinon
        prix par défaut `prix_par_jour`)."""
        interval = self._interval_pour_date(jour)
        return interval.prix if interval else self.prix_par_jour

    def prix_pour_periode(self, date_sortie, date_retour_prevue, include_detail=True):
        """Somme des prix journaliers sur [date_sortie, date_retour_prevue].

        Retourne {total, jours, prix_moyen, detail:[{date, prix, type}]}.
        """
        start = date_sortie.date() if not isinstance(date_sortie, date) else date_sortie
        end = date_retour_prevue.date() if not isinstance(date_retour_prevue, date) else date_retour_prevue
        if start > end:
            start, end = end, start
        total = Decimal('0.00')
        detail = []
        jours = 0
        day = start
        while day <= end:
            interval = self._interval_pour_date(day)
            prix = interval.prix if interval else self.prix_par_jour
            total += prix
            jours += 1
            if include_detail:
                detail.append({
                    'date': day.isoformat(),
                    'prix': str(prix),
                    'type': interval.type if interval else 'DEFAUT',
                })
            day += timedelta(days=1)
        prix_moyen = (total / Decimal(jours)) if jours else self.prix_par_jour
        return {'total': total, 'jours': jours, 'prix_moyen': prix_moyen, 'detail': detail}

    @property
    def traccar_device_id(self):
        """ID du dispositif Traccar lié (lu depuis la table GpsDevice, relation 1:1)."""
        try:
            device = self.gps_device
        except (GpsDevice.DoesNotExist, AttributeError):
            device = None
        return device.traccar_device_id if device else None


class VehiclePriceInterval(models.Model):
    """Période tarifaire saisonnière d'un véhicule.

    Deux types :
      - RECURRENT : répétée chaque année (mois/jour de début et de fin, un
        intervalle peut chevaucher la fin d'année, ex. 15/12 → 15/01) ;
      - ABSOLUTE  : dates précises (date_debut / date_fin).

    Pour une date donnée, une période ABSOLUTE prime sur une RECURRENT ; entre
    deux périodes du même type, la plus chère gagne. Le champ Vehicle.prix_par_jour
    reste le prix par défaut du reste de l'année.
    """

    TYPE_CHOICES = [
        ('RECURRENT', 'Récurrent (chaque année)'),
        ('ABSOLUTE', 'Absolu (dates précises)'),
    ]
    TYPE_RANK = {'RECURRENT': 1, 'ABSOLUTE': 2}

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='price_intervals')
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='RECURRENT', verbose_name="Type")
    prix = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Prix journalier (DH)")

    date_debut = models.DateField(null=True, blank=True, verbose_name="Date de début")
    date_fin = models.DateField(null=True, blank=True, verbose_name="Date de fin")

    mois_debut = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Mois de début (1-12)")
    jour_debut = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Jour de début (1-31)")
    mois_fin = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Mois de fin (1-12)")
    jour_fin = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Jour de fin (1-31)")

    class Meta:
        ordering = ['type', 'date_debut', 'mois_debut']
        verbose_name = "Période tarifaire"
        verbose_name_plural = "Périodes tarifaires"

    def __str__(self):
        if self.type == 'ABSOLUTE':
            return f"{self.date_debut} → {self.date_fin} : {self.prix} DH"
        return f"{self.jour_debut}/{self.mois_debut} → {self.jour_fin}/{self.mois_fin} : {self.prix} DH"

    def contient(self, jour):
        """Vrai si `jour` tombe dans l'intervalle de cette période."""
        if not isinstance(jour, date):
            jour = jour.date()
        if self.type == 'ABSOLUTE':
            return bool(self.date_debut and self.date_fin and self.date_debut <= jour <= self.date_fin)
        debut = (self.mois_debut, self.jour_debut)
        fin = (self.mois_fin, self.jour_fin)
        md = (jour.month, jour.day)
        if debut <= fin:
            return debut <= md <= fin
        return md >= debut or md <= fin  # chevauchement d'année

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.prix is not None and self.prix <= 0:
            errors['prix'] = "Le prix doit être supérieur à zéro."
        if self.type == 'ABSOLUTE':
            if not self.date_debut or not self.date_fin:
                errors['date_fin'] = "Renseignez la date de début et la date de fin."
            elif self.date_debut and self.date_fin and self.date_fin < self.date_debut:
                errors['date_fin'] = "La date de fin doit être postérieure ou égale à la date de début."
        else:
            missing = [f for f in ('mois_debut', 'jour_debut', 'mois_fin', 'jour_fin') if getattr(self, f) is None]
            if missing:
                errors['mois_debut'] = "Renseignez le mois et le jour de début et de fin."
            for label, m, j in (('mois_debut', self.mois_debut, self.jour_debut),
                                ('mois_fin', self.mois_fin, self.jour_fin)):
                if m is not None and not (1 <= m <= 12):
                    errors[label] = "Le mois doit être compris entre 1 et 12."
                if j is not None and not (1 <= j <= 31):
                    errors[label] = "Le jour doit être compris entre 1 et 31."
        if errors:
            raise ValidationError(errors)


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