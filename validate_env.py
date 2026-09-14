#!/usr/bin/env python3
import sys
from pathlib import Path
from typing import List, Tuple


def load_env_file(env_path: Path) -> dict:
    """Load environment variables from a .env file"""
    env_vars = {}
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Remove quotes if present
                    value = value.strip('\"\'')
                    env_vars[key.strip()] = value
    return env_vars


def validate_backend_env() -> Tuple[bool, List[str]]:
    """Validate backend environment variables"""
    errors = []
    env_path = Path(__file__).parent / '.env'
    env_vars = load_env_file(env_path)
    
    # Required backend variables
    required_vars = [
        'DJANGO_SECRET_KEY',
        'DATABASE_URL',
        'DB_PASSWORD',
        'EMAIL_HOST',
        'EMAIL_HOST_USER',
        'EMAIL_HOST_PASSWORD',
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_STORAGE_BUCKET_NAME',
        'AWS_S3_ENDPOINT_URL',
        'AWS_S3_REGION_NAME',
    ]
    
    for var in required_vars:
        if not env_vars.get(var):
            errors.append(f"Missing required backend environment variable: {var}")
    
    # Validate specific variables
    if env_vars.get('EMAIL_PORT'):
        try:
            port = int(env_vars['EMAIL_PORT'])
            if port < 1 or port > 65535:
                errors.append(f"EMAIL_PORT must be between 1 and 65535, got: {port}")
        except ValueError:
            errors.append(f"EMAIL_PORT must be a valid integer, got: {env_vars['EMAIL_PORT']}")
    
    return len(errors) == 0, errors


def validate_frontend_env() -> Tuple[bool, List[str]]:
    """Validate frontend environment variables"""
    errors = []
    
    # Check multiple frontend projects
    frontend_dirs = [
        Path(__file__).parent.parent / 'krini_front',
        Path(__file__).parent.parent / 'kricarfront',
        Path(__file__).parent.parent / 'kricaradmin',
        Path(__file__).parent.parent / 'krinalast-frontend',
    ]
    
    # Required frontend variables (VITE_ prefixed for Vite)
    required_vite_vars = [
        'VITE_API_URL',
        'VITE_GEMINI_API_KEY',
        'VITE_APP_TITLE',
        'VITE_APP_ENV',
        'VITE_ANALYZE_BUNDLE',
        'VITE_ENABLE_AI_FEATURES',
        'VITE_ENABLE_OFFLINE_MODE',
        'VITE_ENABLE_ADVANCED_ANALYTICS',
    ]
    
    # Also check for legacy variables (without VITE_ prefix) for backward compatibility
    legacy_vars = [
        'GEMINI_API_KEY',
        'APP_URL',
    ]
    
    found_any_frontend = False
    for frontend_dir in frontend_dirs:
        if not frontend_dir.exists():
            continue
            
        found_any_frontend = True
        env_path = frontend_dir / '.env'
        env_vars = load_env_file(env_path)
        
        # Check for VITE_ variables
        for var in required_vite_vars:
            if not env_vars.get(var):
                errors.append(
                    f"Missing required frontend environment variable in "
                    f"{frontend_dir.name}: {var}"
                )
        
        # Check for legacy variables (warn but don't fail)
        for var in legacy_vars:
            if not env_vars.get(var):
                # Just a warning, not an error
                pass
    
    if not found_any_frontend:
        errors.append("No frontend directories found")
    
    return len(errors) == 0, errors


def main():
    """Main validation function"""
    print("Validating environment variables for KRINI Mobility OS...")
    print("=" * 60)
    
    # Validate backend
    print("\n1. Backend Environment Validation:")
    backend_valid, backend_errors = validate_backend_env()
    if backend_valid:
        print("[OK] Backend environment variables are valid")
    else:
        print("[ERROR] Backend environment validation failed:")
        for error in backend_errors:
            print(f"  - {error}")
    
    # Validate frontend
    print("\n2. Frontend Environment Validation:")
    frontend_valid, frontend_errors = validate_frontend_env()
    if frontend_valid:
        print("[OK] Frontend environment variables are valid")
    else:
        print("[ERROR] Frontend environment validation failed:")
        for error in frontend_errors:
            print(f"  - {error}")
    
    # Overall result
    print("\n" + "=" * 60)
    if backend_valid and frontend_valid:
        print("[OK] All environment variables are valid!")
        sys.exit(0)
    else:
        print("[ERROR] Environment validation failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
