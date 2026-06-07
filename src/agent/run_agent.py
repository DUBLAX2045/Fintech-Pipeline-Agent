"""
run_agent.py — Ejecuta el agente Fintech en modo consola.

REQUERIDO:
  1. Ollama corriendo: ollama serve
  2. Modelo listo:     ollama pull llama3.2
  3. Pipeline ejecutado: python src/run_pipeline.py

Uso:
  python src/agent/run_agent.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Path setup
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "agent"))

load_dotenv(ROOT / ".env")

OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")


def main():
    print("=" * 60)
    print("  AGENTE FINTECH — Modo Consola")
    print(f"  LLM : Ollama {OLLAMA_MODEL}")
    print(f"  URL : {OLLAMA_URL}")
    print("=" * 60)
    print()

    # Verificar Ollama antes de iniciar
    import requests
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        modelos = [m["name"].split(":")[0] for m in r.json().get("models", [])]
        if OLLAMA_MODEL.split(":")[0] not in modelos:
            print(f"ERROR: Modelo '{OLLAMA_MODEL}' no encontrado en Ollama.")
            print(f"       Ejecuta: ollama pull {OLLAMA_MODEL}")
            sys.exit(1)
        print(f"Ollama OK — modelo {OLLAMA_MODEL} disponible")
    except Exception:
        print(f"ERROR: Ollama no responde en {OLLAMA_URL}")
        print("       Inicia el servidor: ollama serve")
        sys.exit(1)

    from agent.agent import agent_query
    print("\nEscribe tu pregunta (o 'salir' para terminar).\n")

    while True:
        try:
            pregunta = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            break
        if not pregunta:
            continue
        if pregunta.lower() in ("salir", "exit", "quit"):
            print("Hasta luego.")
            break
        try:
            respuesta = agent_query(pregunta)
            print(f"\nAgente: {respuesta}\n")
        except Exception as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    main()
