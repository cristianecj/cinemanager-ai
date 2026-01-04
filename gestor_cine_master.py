import os
import sys
import json
import subprocess
import time
import re
from datetime import datetime
from collections import defaultdict

# --- VARIABLES DE ENTORNO ---
from dotenv import load_dotenv
load_dotenv()                  

# --- LIBRERÍA GOOGLE GENAI ---
from google import genai
from google.genai import types

# --- CONFIGURACIÓN DE USUARIO ---
API_KEY = "GEMINI_API_KEY" 
IDIOMA_TITULO = "original" 
ARCHIVO_MANIFIESTO = ".cine_manifest.json"
MODELO_IA = "gemini-1.5-flash" 

# --- COLORES ---
class Color:
    VERDE = '\033[92m'
    AMARILLO = '\033[93m'
    ROJO = '\033[91m'
    AZUL = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

# --- 1. GESTIÓN DEL MANIFIESTO ---
def cargar_manifiesto(ruta_base):
    ruta_json = os.path.join(ruta_base, ARCHIVO_MANIFIESTO)
    if os.path.exists(ruta_json):
        try:
            with open(ruta_json, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def guardar_manifiesto(ruta_base, datos):
    try:
        with open(os.path.join(ruta_base, ARCHIVO_MANIFIESTO), 'w', encoding='utf-8') as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"{Color.ROJO}Error guardando manifiesto: {e}{Color.RESET}")

def es_formato_oro(nombre):
    # Detecta: Titulo (Año) [Res][Codec][Audio].ext
    return bool(re.match(r'^.+ \(\d{4}\) \[.+\]\[.+\]\[.+\].+$', nombre))

# --- 2. INGENIERÍA (FFPROBE) ---
def obtener_datos_tecnicos(ruta_archivo):
    mapa = {'spa':'Latino','lat':'Latino','eng':'Ingles','jpn':'Japones','fra':'Frances','ita':'Italiano'}
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', ruta_archivo]
        salida = subprocess.check_output(cmd)
        datos = json.loads(salida)
        streams = datos.get('streams', [])
    except: return "[ERROR-METADATA]"

    video = next((s for s in streams if s['codec_type'] == 'video'), None)
    res = "SD"
    codec = "unknown"
    if video:
        w = video.get('width', 0)
        if w >= 3800: res = "4K"
        elif w >= 1900: res = "1080p"
        elif w >= 1260: res = "720p"
        
        c = video.get('codec_name', 'unknown')
        if c in ['hevc', 'h265']: codec = "x265"
        elif c in ['h264', 'avc']: codec = "x264"
        elif c == 'mpeg4': codec = "XviD"
        else: codec = c

    audios = [s for s in streams if s['codec_type'] == 'audio']
    langs = set()
    for a in audios:
        l = a.get('tags', {}).get('language', 'und')
        if l != 'und': langs.add(mapa.get(l, l))
    
    str_lang = '-'.join(sorted(list(langs))) if langs else "Desconocido"
    return f"[{res}][{codec}][{str_lang}]"

# --- 3. INTELIGENCIA (CON RETRY LOGIC) ---
def consultar_gemini_batch(lista_nombres):
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", API_KEY))
    
    prompt = f"""
    Actúa como un API de base de datos de cine (IMDb).
    TAREA: Identificar nombre oficial y año de estreno.
    IDIOMA SALIDA: "{IDIOMA_TITULO}" (Si es 'original', mantén idioma original).
    
    INPUT (JSON List): {json.dumps(lista_nombres)}
    
    OUTPUT (JSON Object estrictamente):
    {{
        "archivo_original_1.ext": {{ "titulo": "Official Title", "anio": "YYYY" }},
        "archivo_2.avi": {{ "titulo": "Title 2", "anio": "YYYY" }}
    }}
    """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODELO_IA,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type='application/json')
            )
            return json.loads(response.text)
        
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait_time = 65 * (attempt + 1)
                print(f"\n{Color.ROJO}⚠️  Cuota excedida. Pausando {wait_time}s...{Color.RESET}")
                time.sleep(wait_time)
                continue
            else:
                print(f"{Color.ROJO}Error API Gemini: {e}{Color.RESET}")
                return {}
    return {}

# --- 4. MÓDULO DE AUDITORÍA FINAL ---
def reportar_duplicados_final(directorio):
    print(f"\n{Color.BOLD}🔎 Iniciando Auditoría de Duplicados...{Color.RESET}")
    print("-" * 60)
    
    inventario = defaultdict(list)
    extensiones = ('.mkv', '.mp4', '.avi', '.mov', '.m4v')
    
    # Escaneo final (post-renombrado)
    for root, dirs, files in os.walk(directorio):
        for f in files:
            if f.lower().endswith(extensiones) and not f.startswith('._'):
                # Extraer "Titulo (Año)" usando Regex
                # Busca todo hasta el año entre paréntesis
                match = re.match(r'^(.* \(\d{4}\))', f)
                if match:
                    base_name = match.group(1)
                    ruta_completa = os.path.join(root, f)
                    size_mb = os.path.getsize(ruta_completa) / (1024 * 1024)
                    inventario[base_name].append({
                        'archivo': f,
                        'size': size_mb,
                        'ruta': ruta_completa
                    })

    duplicados = 0
    for titulo, items in inventario.items():
        if len(items) > 1:
            duplicados += 1
            print(f"\n🚨 {Color.MAGENTA}{Color.BOLD}CONFLICTO: {titulo}{Color.RESET}")
            
            # Ordenar por tamaño (mayor tamaño suele ser mejor calidad)
            items_ordenados = sorted(items, key=lambda x: x['size'], reverse=True)
            
            for item in items_ordenados:
                nombre = item['archivo']
                peso = f"{item['size']:.1f} MB"
                
                # Extraer info técnica visualmente del nombre
                # Asumimos que el nombre ya tiene [1080p] etc.
                tech_info = ""
                if "[" in nombre:
                    tech_info = nombre[nombre.find("["):]
                
                print(f"   📄 {Color.AMARILLO}{peso:<10}{Color.RESET} | {tech_info}")

    if duplicados == 0:
        print(f"\n{Color.VERDE}✨ ¡Excelente! No se encontraron duplicados.{Color.RESET}")
    else:
        print(f"\n{Color.BOLD}Resumen:{Color.RESET} Se detectaron {duplicados} grupos de películas repetidas.")
        print("Revisa la lista y borra manualmente las versiones inferiores.")

# --- 5. MOTOR PRINCIPAL ---
def procesar_biblioteca(directorio, ejecutar=False):
    key = os.environ.get("GEMINI_API_KEY", API_KEY)
    if key == "TU_API_KEY_AQUI":
        print(f"{Color.ROJO}Falta API KEY.{Color.RESET}"); return

    manifiesto = cargar_manifiesto(directorio)
    archivos_ia = []
    
    print(f"{Color.BOLD}1. Escaneando directorio...{Color.RESET}")
    for root, dirs, files in os.walk(directorio):
        for f in files:
            if f.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.m4v')) and not f.startswith('._'):
                if f in manifiesto: continue
                if es_formato_oro(f):
                    manifiesto[f] = {"fecha": str(datetime.now()), "status": "adoptado"}
                    continue
                archivos_ia.append(os.path.join(root, f))
    
    print(f"   Pendientes de procesar: {Color.AMARILLO}{len(archivos_ia)}{Color.RESET}")
    
    if archivos_ia: 
        # PROCESAR POR LOTES
        LOTE = 10 
        nombres_usados = defaultdict(int)
        
        for i in range(0, len(archivos_ia), LOTE):
            lote = archivos_ia[i:i+LOTE]
            nombres_lote = [os.path.basename(p) for p in lote]
            
            print(f"\n{Color.AZUL}>>> Consultando IA (Lote {i//LOTE + 1})...{Color.RESET}")
            info_ia = consultar_gemini_batch(nombres_lote)
            
            for ruta_orig in lote:
                fname = os.path.basename(ruta_orig)
                ext = os.path.splitext(fname)[1]
                tecnicos = obtener_datos_tecnicos(ruta_orig)
                
                datos = info_ia.get(fname)
                if datos and datos.get('titulo'):
                    titulo = datos['titulo'].replace(':', ' -').replace('/', '-').replace('?', '')
                    anio = datos['anio']
                    
                    base = f"{titulo} ({anio})"
                    if nombres_usados[base] > 0:
                        nuevo_nombre = f"{base} ({nombres_usados[base]}) {tecnicos}{ext}"
                    else:
                        nuevo_nombre = f"{base} {tecnicos}{ext}"
                    nombres_usados[base] += 1
                    
                    if fname != nuevo_nombre:
                        print(f"   {Color.AMARILLO}Cambio:{Color.RESET} {fname} -> {Color.VERDE}{nuevo_nombre}{Color.RESET}")
                        if ejecutar:
                            nueva_ruta = os.path.join(os.path.dirname(ruta_orig), nuevo_nombre)
                            try:
                                if not os.path.exists(nueva_ruta):
                                    os.rename(ruta_orig, nueva_ruta)
                                    manifiesto[nuevo_nombre] = {"origen": fname, "fecha": str(datetime.now())}
                                else:
                                    print(f"   {Color.ROJO}Destino existe.{Color.RESET}")
                            except OSError as e: print(f"Error: {e}")
                    else:
                        if ejecutar: manifiesto[fname] = {"status": "verificado"}
                else:
                    print(f"   {Color.ROJO}IA Falló con: {fname}{Color.RESET}")
            
            time.sleep(5) 

        if ejecutar:
            guardar_manifiesto(directorio, manifiesto)
            print(f"\n✅ Manifiesto actualizado.")
    else:
        print("✅ Todo al día con el renombrado.")

    # --- EJECUTAR AUDITORÍA FINAL ---
    reportar_duplicados_final(directorio)

if __name__ == "__main__":
    ruta = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != 'run' else "."
    ejecutar_real = 'run' in sys.argv
    if ejecutar_real: print(f"{Color.ROJO}!!! EJECUCIÓN REAL !!!{Color.RESET}"); time.sleep(2)
    procesar_biblioteca(ruta, ejecutar=ejecutar_real)