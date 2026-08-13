from django.db import models
from agency.models import Agency, CustomUser
from fleet.models import Vehicle
from clients.models import Client
from django.utils import timezone
from car_rental_backend.uploads import pdf_job_upload_to


def _release_vehicle(vehicle, exclude_contract=None):
    """Remet un véhicule en 'Available' à l'annulation/terminaison d'un contrat
    ou d'une réservation.

    Ne libère pas le véhicule s'il reste une location en cours (EN_COURS) :
    il est alors réellement loué par un autre contrat. `exclude_contract` exclut
    le contrat en cours de modification (non encore commité au moment de l'appel).
    """
    active_rental = Contract.objects.filter(vehicle=vehicle, statut='EN_COURS')
    if exclude_contract is not None and exclude_contract.pk is not None:
        active_rental = active_rental.exclude(pk=exclude_contract.pk)
    if active_rental.exists():
        return
    if vehicle.statut != 'Available':
        vehicle.statut = 'Available'
        vehicle.save(update_fields=['statut'])

class Contract(models.Model):
    # حالات العقد
    STATUS_CHOICES =[
        ('RESERVE', 'محجوزة مسبقاً'),
        ('EN_COURS', 'في طور الكراء (جارية)'),
        ('TERMINE', 'منتهية (تم إرجاع السيارة)'),
        ('ANNULE', 'ملغاة'),
    ]

    # مستويات الوقود (0/8 إلى 8/8 كما في الورقة)
    FUEL_LEVELS = [
        ('0/8', '0/8 (Vide)'),
        ('1/8', '1/8'),
        ('2/8', '2/8 (1/4)'),
        ('3/8', '3/8'),
        ('4/8', '4/8 (1/2)'),
        ('5/8', '5/8'),
        ('6/8', '6/8 (3/4)'),
        ('7/8', '7/8'),
        ('8/8', '8/8 (Plein)'),
    ]

    # 1. روابط الـ Multi-Agency والمستخدمين
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='contracts')
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, verbose_name="الموظف الذي أنشأ العقد")
    
    # 2. أطراف العقد (السيارة والزبون)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='contracts', verbose_name="السيارة")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='contracts', verbose_name="الزبون المكتري")
    deuxieme_chauffeur = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='contracts_as_second', verbose_name="السائق الثاني")

    # 3. التواريخ والمدة
    date_sortie = models.DateTimeField(default=timezone.now, verbose_name="تاريخ ووقت التسليم")
    date_retour_prevue = models.DateTimeField(verbose_name="تاريخ ووقت الإرجاع المتوقع")
    date_retour_effective = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الإرجاع الفعلي") # يتم ملؤه عند إرجاع السيارة
    jours = models.IntegerField(default=1, verbose_name="عدد الأيام")

    # 4. الحالة عند التسليم (Livraison)
    km_sortie = models.IntegerField(verbose_name="الكيلومتراج عند الخروج")
    carburant_sortie = models.CharField(max_length=10, choices=FUEL_LEVELS, default='2/8', verbose_name="الوقود عند الخروج")
    degats_depart = models.TextField(null=True, blank=True, verbose_name="ملاحظات/أضرار عند الخروج")
    
    # الإكسسوارات (Accessories)
    roue_secours = models.BooleanField(default=False, verbose_name="Roue de Secours")
    cric = models.BooleanField(default=False, verbose_name="Cric")
    manivelle = models.BooleanField(default=False, verbose_name="Manivelle")
    gilet = models.BooleanField(default=False, verbose_name="Gilet")
    triangle = models.BooleanField(default=False, verbose_name="Triangle")
    extincteur = models.BooleanField(default=False, verbose_name="Extincteur")
    papiers = models.BooleanField(default=False, verbose_name="Papiers")
    cles = models.BooleanField(default=False, verbose_name="Clés")

    # 5. الحالة عند الإرجاع (Réception)
    km_retour = models.IntegerField(null=True, blank=True, verbose_name="الكيلومتراج عند الإرجاع")
    carburant_retour = models.CharField(max_length=10, choices=FUEL_LEVELS, null=True, blank=True, verbose_name="الوقود عند الإرجاع")
    degats_retour = models.TextField(null=True, blank=True, verbose_name="ملاحظات/أضرار عند الإرجاع")

    # الإكسسوارات عند الإرجاع (Accessories Return)
    roue_secours_retour = models.BooleanField(default=False, verbose_name="Roue de Secours (Retour)")
    cric_retour = models.BooleanField(default=False, verbose_name="Cric (Retour)")
    manivelle_retour = models.BooleanField(default=False, verbose_name="Manivelle (Retour)")
    gilet_retour = models.BooleanField(default=False, verbose_name="Gilet (Retour)")
    triangle_retour = models.BooleanField(default=False, verbose_name="Triangle (Retour)")
    extincteur_retour = models.BooleanField(default=False, verbose_name="Extincteur (Retour)")
    papiers_retour = models.BooleanField(default=False, verbose_name="Papiers (Retour)")
    cles_retour = models.BooleanField(default=False, verbose_name="Clés (Retour)")

    # 6. الحسابات المالية (Finance)
    prix_par_jour = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="ثمن الكراء اليومي المتفق عليه")
    montant_total = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ الإجمالي")
    montant_paye = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="المبلغ المدفوع (التسبيق)")
    reste_a_payer = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="المبلغ المتبقي")
    caution = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="مبلغ الضمانة (Caution)")
    methode_paiement = models.CharField(max_length=50, default="Espèce", verbose_name="طريقة الدفع") # Espèce, Chèque, TPE, Virement

    # 7. حالة العقد
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='EN_COURS', verbose_name="حالة العقد")
    date_creation = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # 1. حساب المبلغ الإجمالي والمتبقي تلقائياً قبل حفظ العقد
        if self.prix_par_jour and self.jours:
            self.montant_total = self.prix_par_jour * self.jours
            self.reste_a_payer = self.montant_total - self.montant_paye
            
        # 2. تحديث الكيلومتراج الخاص بالسيارة إذا تم إرجاعها
        if self.statut == 'TERMINE' and self.km_retour:
            self.vehicle.kilometrage = self.km_retour
            self.vehicle.statut = 'Available'
            self.vehicle.save()
            
        # 3. تحديث حالة السيارة إذا كانت في طور الكراء أو ملغاة
        elif self.statut == 'EN_COURS':
            self.vehicle.statut = 'Rented'
            self.vehicle.save()
            
        elif self.statut == 'ANNULE':
            _release_vehicle(self.vehicle, exclude_contract=self)

        super(Contract, self).save(*args, **kwargs)

    def __str__(self):
        return f"عقد رقم {self.id} - {self.client.nom} - {self.vehicle.matricule}"


class ContractDamage(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='damages')
    type = models.CharField(max_length=10, choices=[('DEPART', 'Départ'), ('RETOUR', 'Retour')], default='DEPART')
    x = models.FloatField(verbose_name="الإحداثي X (%)")
    y = models.FloatField(verbose_name="الإحداثي Y (%)")
    description = models.TextField(null=True, blank=True, verbose_name="وصف الضرر")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Damage {self.type} for Contract {self.contract.id} at ({self.x}, {self.y})"


class BookingRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'En attente'),
        ('CONTACTED', 'Contacté'),
        ('CONFIRMED', 'Confirmé'),
        ('CANCELLED', 'Annulé'),
    ]

    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='booking_requests', verbose_name="Agence")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name='booking_requests', verbose_name="Véhicule")
    nom = models.CharField(max_length=50, verbose_name="Nom")
    prenom = models.CharField(max_length=50, verbose_name="Prénom")
    telephone = models.CharField(max_length=20, verbose_name="Téléphone")
    email = models.EmailField(null=True, blank=True, verbose_name="Email")
    message = models.TextField(null=True, blank=True, verbose_name="Message")
    date_sortie = models.DateTimeField(verbose_name="Date de sortie souhaitée")
    date_retour_prevue = models.DateTimeField(verbose_name="Date de retour souhaitée")
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name="Statut")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Demande #{self.id} - {self.nom} {self.prenom}"


class Reservation(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'En attente'),
        ('CONFIRMED', 'Confirmée'),
        ('CANCELLED', 'Annulée'),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='reservations', verbose_name="Client")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='reservations', verbose_name="Véhicule")
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='reservations', verbose_name="Agence")
    date_sortie = models.DateTimeField(verbose_name="Date de sortie")
    date_retour_prevue = models.DateTimeField(verbose_name="Date de retour prévue")
    prix_par_jour = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Prix par jour")
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name="Statut")
    notes = models.TextField(null=True, blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        if self.statut == 'CANCELLED':
            _release_vehicle(self.vehicle)
        return result

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Réservation #{self.id} - {self.client.nom} - {self.vehicle.matricule}"


class PdfJob(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'En attente'),
        ('PROCESSING', 'En cours'),
        ('READY', 'Prêt'),
        ('ERROR', 'Erreur'),
    ]
    JOB_TYPES = [
        ('contract', 'Contrat'),
        ('receipt', 'Reçu réservation'),
    ]

    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='pdf_jobs')
    job_type = models.CharField(max_length=20, choices=JOB_TYPES)
    with_cachet = models.BooleanField(default=False, verbose_name="Avec cachet")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    error_message = models.TextField(null=True, blank=True)
    pdf_file = models.FileField(upload_to=pdf_job_upload_to, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PdfJob #{self.id} - {self.job_type} - Contract {self.contract.id} - {self.status}"
