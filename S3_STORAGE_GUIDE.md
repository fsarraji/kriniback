# Guide : Activer Supabase Storage (S3) pour les images

## Pourquoi

Les images (`vehicles_images/`, scans clients, cachets d'agence) sont stockées **localement** sur le disque de Render, qui est **éphémère** : tout fichier disparaît à chaque redéploiement. Résultat : les URL d'images renvoient `404` avec du `text/html` → le frontend web bloque la lecture (erreur CORB dans la console) et les images n'apparaissent plus.

L'activation du S3 est déjà prévue dans le code (`car_rental_backend/settings.py`) : il suffit que les variables d'environnement `AWS_*` soient présentes sur Render.

## Étapes

### 1. Créer un bucket public dans Supabase Storage

1. Console Supabase → votre projet (celui du backend).
2. **Storage** → **New bucket** :
   - Nom : par ex. `krinimediabucket`
   - **Public bucket : ON** (indispensable pour des URL publiques sans signature)
3. **Storage → Policies** → créer une politique `SELECT` **public** sur ce bucket (Allow all, pour lecture publique).

### 2. Récupérer les clés S3 (compatibles)

1. Console Supabase → **Storage → Settings → S3 Access Keys**.
2. **New access key** (éventuellement limité au bucket) → copier :
   - `Access Key ID` (ex. préfixe `sb_access_...`)
   - `Secret Access Key` (ex. préfixe `sb_secret_...`) — **affiché une seule fois**
3. ⚠️ L'`Access Key ID` et le `Secret` sont générés **par paire** : une paire non correspondante produit `SignatureDoesNotMatch` (HTTP 403) à l'écriture.
4. Endpoint : `https://<PROJECT_REF>.supabase.co/storage/v1/s3`
   - Exemple avec le ref visible dans l'URL du projet : `https://gojkevxwwoimalftpuzg.supabase.co/storage/v1/s3`
   - Région : celle du projet (ex. `eu-central-1`)

### 3. Configurer les variables d'environnement sur Render

Service web du backend → **Environment** → ajouter :

| Variable | Valeur |
|---|---|
| `AWS_ACCESS_KEY_ID` | `<Access Key ID S3>` |
| `AWS_SECRET_ACCESS_KEY` | `<Secret Access Key S3>` |
| `AWS_STORAGE_BUCKET_NAME` | `krinimediabucket` |
| `AWS_S3_ENDPOINT_URL` | `https://<PROJECT_REF>.supabase.co/storage/v1/s3` |
| `AWS_S3_REGION_NAME` | `eu-central-1` |

⚠️ **Redéploiement nécessaire** : le serveur doit relire ces variables pour basculer sur le stockage S3.

### 4. Vérifier que le stockage S3 est actif

Après déploiement, lancer la commande de diagnostic incluse dans le repo :

```
python manage.py check_storage
```

Attendu :
- `Backend actif : car_rental_backend.storage.OptimizedS3Storage`
- `Variables S3/Supabase définies : OUI`
- `=> Stockage actif : SUPABASE STORAGE (S3). Les images persisteront.`

### 5. Re-uploader les images existantes

Les anciennes images sont perdues (disque éphémère). Il faut ré-uploader les photos via l'app (écran véhicule, scan documents, réglages agence) après la bascule. Les nouvelles images seront alors servies directement depuis Supabase avec le bon `Content-Type` d'image (plus de 404 HTML, plus de CORB).

## Commentaires de code pertinents

- Bascule S3 : `car_rental_backend/settings.py` (bloc `if AWS_ACCESS_KEY_ID and ...`)
  - ⚠️ **Django ≥ 5.1** : `DEFAULT_FILE_STORAGE` seul est **ignoré**. Il faut définir
    `STORAGES['default']['BACKEND'] = 'car_rental_backend.storage.OptimizedS3Storage'` (fait dans ce repo).
  - ℹ️ Toutes les images sont optimisées à l'écriture (EXIF, redimensionnement max 1600px,
    compression JPEG) via `car_rental_backend/images.py`.
  - ℹ️ Chaque agence possède un dossier à la racine media : `<agence>/vehicles_images`,
    `<agence>/clients_documents/cin|permis`, `<agence>/agency_logos`, `<agence>/agency_cachets`
    (voir `car_rental_backend/uploads.py`).
- Diagnostic : `fleet/management/commands/check_storage.py`
- URL des images côté frontend : `src/apiUrl.js` (`resolveMediaUrl`)

## Dépannage

- `=> Stockage actif : DISQUE LOCAL` alors que les variables S3 sont définies → vérifier que
  le code déployé contient bien le bloc `STORAGES` (Django ≥ 5.1), puis **redéployer** Render
  (Manual Deploy → Deploy latest commit) : modifier les variables d'env ne redémarre pas le service.
- `SignatureDoesNotMatch` / `403 Forbidden` au test d'écriture → l'`Access Key ID` et le
  `Secret Access Key` ne correspondent pas : régénérer la paire dans **Storage → Settings → S3 Access Keys**.
