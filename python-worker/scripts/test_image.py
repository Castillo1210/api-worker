import os
import sys
import argparse

# Ensure current directory is in PYTHONPATH so app imports work
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.quality_validator import QualityValidator

def print_result_detail(result, filepath):
    print("=" * 60)
    print(f"ANÁLISIS DE IMAGEN: {os.path.basename(filepath)}")
    print(f"Ruta: {filepath}")
    print("-" * 60)
    print(f"¿Es válida?          : {'SÍ' if result.is_valid else 'NO'}")
    print(f"Tipo de captura      : {result.capture_type}")
    print(f"Problemas (Issues)   : {result.issues if result.issues else 'Ninguno'}")
    print("-" * 60)
    print("MÉTRICAS Y PUNTAJES:")
    metrics = result.metrics or {}
    capture_scores = result.capture_scores or {}
    
    # Print capture scores
    print(f"  Puntaje Foto (photo)       : {capture_scores.get('photo_score', 0.0):.4f}")
    print(f"  Puntaje Foto Pantalla      : {capture_scores.get('photo_of_screen_score', 0.0):.4f}")
    print(f"  Puntaje Screenshot         : {capture_scores.get('screenshot_score', 0.0):.4f}")
    
    # Print specific classifier metrics
    print(f"  Puntaje Moiré FFT          : {metrics.get('fft_moire_score', 0.0):.4f} (picos: {metrics.get('fft_peak_count', 0)})")
    print(f"  Frecuencia Máxima FFT      : {metrics.get('fft_max_peak_val', 0.0):.4f}")
    print(f"  Resolución                 : {metrics.get('resolution', 'unknown')}")
    print(f"  Aspect Ratio               : {metrics.get('aspect_ratio', 0.0):.2f}")
    print(f"  Nitidez (Blur Score)       : {metrics.get('blur_score', 0.0):.2f}")
    print(f"  Contraste                  : {metrics.get('contrast', 0.0):.2f}")
    print(f"  Brillo/Reflejo (Glare)     : {metrics.get('glare_ratio', 0.0):.4f}")
    print(f"  Confianza de Documento     : {metrics.get('document_confidence', 0.0):.4f}")
    print("=" * 60)

def print_results_table(results):
    # Print in simple ascii table format
    headers = ["Nombre de Archivo", "Válida", "Tipo Captura", "Moiré FFT", "Nitidez", "Problemas"]
    row_format = "{:<30} | {:<7} | {:<15} | {:<10} | {:<8} | {:<20}"
    
    print("\n" + "=" * 105)
    print(row_format.format(*headers))
    print("-" * 105)
    
    for filename, res in results:
        is_valid = "SÍ" if res.is_valid else "NO"
        cap_type = str(res.capture_type)
        metrics = res.metrics or {}
        moire = f"{metrics.get('fft_moire_score', 0.0):.2f} ({metrics.get('fft_peak_count', 0)})"
        blur = f"{metrics.get('blur_score', 0.0):.1f}"
        issues = ", ".join(res.issues) if res.issues else "Ok"
        
        # Truncate filename if too long
        display_name = filename[:27] + "..." if len(filename) > 30 else filename
        print(row_format.format(display_name, is_valid, cap_type, moire, blur, issues))
        
    print("=" * 105 + "\n")

def process_path(path):
    validator = QualityValidator()
    
    if os.path.isfile(path):
        with open(path, "rb") as f:
            file_bytes = f.read()
        result = validator.validate(file_bytes, "image", capture_mode="auto")
        print_result_detail(result, path)
    elif os.path.isdir(path):
        valid_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        files = [
            os.path.join(path, f) for f in os.listdir(path)
            if os.path.splitext(f.lower())[1] in valid_extensions
        ]
        
        if not files:
            print(f"No se encontraron imágenes en el directorio: {path}")
            return
            
        print(f"Procesando {len(files)} imágenes...")
        results = []
        for filepath in sorted(files):
            try:
                with open(filepath, "rb") as f:
                    file_bytes = f.read()
                result = validator.validate(file_bytes, "image", capture_mode="auto")
                results.append((os.path.basename(filepath), result))
            except Exception as e:
                print(f"Error procesando {os.path.basename(filepath)}: {e}")
                
        print_results_table(results)
    else:
        print(f"Ruta no válida: {path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validar calidad y tipo de captura de imagen(es)")
    parser.add_argument("--path", required=True, help="Ruta al archivo de imagen o directorio")
    args = parser.parse_args()
    
    process_path(args.path)
