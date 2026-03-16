#!/usr/bin/env python
"""Seed the eval_dataset table with golden test cases from the manual testing guide.

Usage:
    python scripts/seed_eval_dataset.py [options]

Options:
    --db PATH       Path to SQLite database (default: data/localforge.db)
    --clear         Remove all seed entries before inserting
    --dry-run       Show what would be inserted without touching the DB
    --section NAME  Only seed entries for a specific section

The script is idempotent: entries whose input_text already exists with source=seed
are skipped on re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Ensure project root is on sys.path when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import init_db


@dataclass
class EvalCase:
    input_text: str
    expected_output: str
    section: str
    expected_categories: list[str]
    expected_tools: list[str] = field(default_factory=list)
    language: str = "es"
    eval_types: list[str] = field(default_factory=lambda: ["classify", "e2e"])


# ---------------------------------------------------------------------------
# Test case catalogue (~82 cases)
# ---------------------------------------------------------------------------

CASES: list[EvalCase] = [
    # §chat — Chat basico / sin tools (5)
    EvalCase(
        "Hola, como estas?",
        "Respuesta conversacional amigable",
        "chat",
        ["none"],
        eval_types=["classify", "e2e"],
    ),
    EvalCase(
        "Contame un chiste",
        "Un chiste o respuesta humoristica",
        "chat",
        ["none"],
        eval_types=["classify", "e2e"],
    ),
    EvalCase(
        "Cual es la capital de Francia?", "Paris", "chat", ["none"], eval_types=["classify", "e2e"]
    ),
    EvalCase(
        "Explicame que es la fotosintesis",
        "Explicacion del proceso de fotosintesis",
        "chat",
        ["none"],
        eval_types=["classify", "e2e"],
    ),
    EvalCase(
        "Gracias por la ayuda",
        "Respuesta de despedida cortes",
        "chat",
        ["none"],
        eval_types=["classify", "e2e"],
    ),
    # §math — Calculadora (8)
    EvalCase(
        "Cuanto es 15 * 7 + 3?",
        "108",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Raiz cuadrada de 144",
        "12",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "sin(pi/2)",
        "1 o 1.0",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "2 ** 10", "1024", "math", ["math"], ["calculate"], eval_types=["classify", "tools", "e2e"]
    ),
    EvalCase(
        "Cuanto es el 15% de 230?",
        "34.5",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "sqrt(2)",
        "Aproximadamente 1.414",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "floor(3.7) + ceil(2.1)",
        "6",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Cuanto es 100 / 3?",
        "33.33",
        "math",
        ["math"],
        ["calculate"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §time — Fecha, hora, recordatorios (8)
    EvalCase(
        "Que hora es?",
        "La hora actual con timezone",
        "time",
        ["time"],
        ["get_current_datetime"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que hora es en Tokio?",
        "Hora en Asia/Tokyo",
        "time",
        ["time"],
        ["get_current_datetime"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que dia es hoy?",
        "La fecha de hoy",
        "time",
        ["time"],
        ["get_current_datetime"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que hora es en Buenos Aires?",
        "Hora en Argentina",
        "time",
        ["time"],
        ["get_current_datetime"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Si son las 14:30 en Madrid, que hora es en Nueva York?",
        "Hora convertida (generalmente -6h)",
        "time",
        ["time"],
        ["convert_timezone"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Recordame revisar los logs en 5 minutos",
        "Confirmacion con hora del reminder",
        "time",
        ["time"],
        ["schedule_task"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que recordatorios tengo?",
        "Lista de recordatorios activos",
        "time",
        ["time"],
        ["list_schedules"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Recordame manana a las 9am enviar el reporte",
        "Confirmacion con hora agendada",
        "time",
        ["time"],
        ["schedule_task"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §weather — Clima (4)
    EvalCase(
        "Clima en Buenos Aires",
        "Reporte con temperatura, humedad, viento",
        "weather",
        ["weather"],
        ["get_weather"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Weather in New York",
        "Weather report in English",
        "weather",
        ["weather"],
        ["get_weather"],
        language="en",
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Como esta el clima en Londres hoy?",
        "Reporte meteorologico de Londres",
        "weather",
        ["weather"],
        ["get_weather"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Hace frio en Moscu?",
        "Info de temperatura de Moscu",
        "weather",
        ["weather"],
        ["get_weather"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §search — Busqueda web (4)
    EvalCase(
        "Busca noticias sobre inteligencia artificial",
        "Resultados con titulos y URLs",
        "search",
        ["news"],
        ["search_news"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Search for Python 3.13 features",
        "Results about Python 3.13",
        "search",
        ["search"],
        ["web_search"],
        language="en",
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que es FastAPI y para que sirve?",
        "Informacion sobre FastAPI",
        "search",
        ["search", "knowledge"],
        ["web_search"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Busca tutoriales de Docker compose",
        "Resultados de tutoriales",
        "search",
        ["search"],
        ["web_search"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §notes — Notas CRUD (7)
    EvalCase(
        "Guarda una nota: Reunion lunes con Juan sobre el proyecto",
        "Confirmacion de nota guardada con ID",
        "notes",
        ["notes"],
        ["save_note"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Mostra mis notas",
        "Lista de notas con ID y titulo",
        "notes",
        ["notes"],
        ["list_notes"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Busca notas sobre reunion",
        "Notas que coincidan con reunion",
        "notes",
        ["notes"],
        ["search_notes"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Lee la nota 1",
        "Contenido completo de la nota",
        "notes",
        ["notes"],
        ["get_note"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Borra la nota 1",
        "Confirmacion de eliminacion",
        "notes",
        ["notes"],
        ["delete_note"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Guarda una nota titulada Compras con contenido: Leche, pan, huevos",
        "Nota guardada con titulo Compras",
        "notes",
        ["notes"],
        ["save_note"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Tengo alguna nota guardada?",
        "Lista o mensaje de sin notas",
        "notes",
        ["notes"],
        ["list_notes"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §projects — Proyectos (12)
    EvalCase(
        "Crea un proyecto llamado Backend API con descripcion: Refactoring del auth",
        "Confirmacion con ID del proyecto",
        "projects",
        ["projects"],
        ["create_project"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Lista mis proyectos",
        "Lista de proyectos con estado",
        "projects",
        ["projects"],
        ["list_projects"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Agrega una tarea al proyecto Backend API: Migrar JWT a OAuth2",
        "Confirmacion de tarea agregada",
        "projects",
        ["projects"],
        ["add_task"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Como va el proyecto Backend API?",
        "Progreso con tareas y porcentaje",
        "projects",
        ["projects"],
        ["get_project", "project_progress"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Marca como hecha la tarea 1",
        "Confirmacion de actualizacion",
        "projects",
        ["projects"],
        ["update_task"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Borra la tarea 2",
        "Confirmacion de eliminacion",
        "projects",
        ["projects"],
        ["delete_task"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Agrega una nota al proyecto Backend API: La migracion requiere 3 endpoints",
        "Nota agregada al proyecto",
        "projects",
        ["projects"],
        ["add_project_note"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Mostra las notas del proyecto Backend API",
        "Lista de notas del proyecto",
        "projects",
        ["projects"],
        ["list_project_notes"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Busca notas del proyecto sobre endpoints",
        "Notas que coincidan",
        "projects",
        ["projects"],
        ["search_project_notes"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Archiva el proyecto Backend API",
        "Proyecto archivado",
        "projects",
        ["projects"],
        ["update_project_status"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Mostra los proyectos archivados",
        "Lista filtrada por archivados",
        "projects",
        ["projects"],
        ["list_projects"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que tareas pendientes tiene el proyecto Backend API?",
        "Tareas con estado pending",
        "projects",
        ["projects"],
        ["get_project", "project_progress"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §selfcode — Introspeccion (7)
    EvalCase(
        "Cual es tu version actual?",
        "Info de git, branch, version",
        "selfcode",
        ["selfcode"],
        ["get_version_info"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Mostra la estructura de app/skills/",
        "Arbol de archivos",
        "selfcode",
        ["selfcode"],
        ["list_source_files"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Busca en el codigo donde se define classify_intent",
        "Resultados con archivo y linea",
        "selfcode",
        ["selfcode"],
        ["search_source_code"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Cual es la configuracion runtime?",
        "Configuracion sin secretos",
        "selfcode",
        ["selfcode"],
        ["get_runtime_config"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Como esta la salud del sistema?",
        "Estado de Ollama, DB, embeddings",
        "selfcode",
        ["selfcode"],
        ["get_system_health"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Mostra los ultimos logs de error",
        "Ultimas lineas del log",
        "selfcode",
        ["selfcode"],
        ["get_recent_logs"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Mostra el outline de app/config.py",
        "Clases, funciones, lineas",
        "selfcode",
        ["selfcode"],
        ["get_file_outline"],
        eval_types=["classify", "tools", "e2e"],
    ),
    # §github — GitHub MCP (3)
    EvalCase(
        "Lista las issues abiertas de mi repo",
        "Lista de issues con numero y titulo",
        "github",
        ["github"],
        ["list_issues"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Busca repositorios sobre FastAPI",
        "Repos con estrellas y descripcion",
        "github",
        ["github", "search"],
        ["search_repositories"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Mostra los pull requests abiertos",
        "PRs con titulo y estado",
        "github",
        ["github"],
        ["list_pull_requests"],
        eval_types=["classify", "tools"],
    ),
    # §tools — Meta-herramientas (3)
    EvalCase(
        "Que categorias de herramientas tenes?",
        "Lista de categorias con cantidad",
        "tools",
        ["tools"],
        ["list_tool_categories"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Que herramientas hay en la categoria projects?",
        "Nombres de tools en projects",
        "tools",
        ["tools"],
        ["list_category_tools"],
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase(
        "Como puedo buscar cosas en la web?",
        "Info sobre capacidades de busqueda",
        "tools",
        ["tools", "search"],
        ["list_category_tools", "web_search"],
        eval_types=["classify", "tools"],
    ),
    # §expand — MCP Registry (3)
    EvalCase(
        "Lista los servidores MCP activos",
        "Servidores con estado",
        "expand",
        ["expand"],
        ["list_mcp_servers"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Busca servidores MCP para Slack",
        "Resultados de Smithery",
        "expand",
        ["expand"],
        ["search_mcp_registry"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Que servidores MCP tengo configurados?",
        "Lista de servidores",
        "expand",
        ["expand"],
        ["list_mcp_servers"],
        eval_types=["classify", "tools"],
    ),
    # §evaluation — Eval Pipeline (4)
    EvalCase(
        "Mostra las estadisticas del dataset de eval",
        "Conteos por tipo de entry",
        "evaluation",
        ["evaluation"],
        ["get_dataset_stats"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Cuantas interacciones fallaron esta semana?",
        "Lista de failures recientes",
        "evaluation",
        ["evaluation"],
        ["list_recent_failures"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Mostra el dashboard de metricas",
        "Tendencias y pass rate",
        "evaluation",
        ["evaluation"],
        ["get_dashboard_stats"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Mostra el resumen de evaluaciones de los ultimos 7 dias",
        "Resumen con metricas",
        "evaluation",
        ["evaluation"],
        ["get_eval_summary"],
        eval_types=["classify", "tools"],
    ),
    # §automation — Automatizacion (3)
    EvalCase(
        "Mostra las reglas de automatizacion",
        "Lista con estado on/off",
        "automation",
        ["automation"],
        ["list_automation_rules"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Mostra el log de automatizacion",
        "Historial de ejecuciones",
        "automation",
        ["automation"],
        ["get_automation_log"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Desactiva la regla de alertas",
        "Confirmacion de cambio",
        "automation",
        ["automation"],
        ["toggle_automation_rule"],
        eval_types=["classify", "tools"],
    ),
    # §knowledge — Grafo de conocimiento (2)
    EvalCase(
        "Busca en el grafo de conocimiento sobre Python",
        "Entidades y relaciones encontradas",
        "knowledge",
        ["knowledge"],
        ["search_knowledge_graph"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Que entidades conoces sobre el proyecto Backend?",
        "Entidades relacionadas",
        "knowledge",
        ["knowledge"],
        ["search_knowledge_graph"],
        eval_types=["classify", "tools"],
    ),
    # §multicategory — Multi-categoria (5)
    EvalCase(
        "Necesito crear una issue en GitHub para el proyecto Backend API sobre el bug del login",
        "Accion coordinada",
        "multicategory",
        ["projects", "github"],
        ["create_issue", "get_project"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Calcula cuanto es 15% de 230 y busca que impuesto aplica",
        "Calculo + busqueda",
        "multicategory",
        ["math", "search"],
        ["calculate", "web_search"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Que hora es y como esta el clima?",
        "Hora y clima",
        "multicategory",
        ["time", "weather"],
        ["get_current_datetime", "get_weather"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Guarda una nota sobre el progreso del proyecto Backend",
        "Nota con contexto de proyecto",
        "multicategory",
        ["notes", "projects"],
        ["save_note", "get_project"],
        eval_types=["classify", "tools"],
    ),
    EvalCase(
        "Busca en mis notas y en la web sobre OAuth2",
        "Resultados de ambas fuentes",
        "multicategory",
        ["notes", "search"],
        ["search_notes", "web_search"],
        eval_types=["classify", "tools"],
    ),
    # §edge — Edge cases (4)
    EvalCase(
        "https://example.com", "Contenido de la URL", "edge", ["fetch"], eval_types=["classify"]
    ),
    EvalCase(
        "What time is it in London?",
        "Current time in London",
        "edge",
        ["time"],
        ["get_current_datetime"],
        language="en",
        eval_types=["classify", "tools", "e2e"],
    ),
    EvalCase("你好", "Respuesta", "edge", ["none"], eval_types=["classify"]),
    EvalCase(
        "Necesito ayuda con muchas cosas: ver la hora, el clima, mis notas y el estado de mis proyectos",
        "Respuesta multi-tool",
        "edge",
        ["time", "weather", "notes", "projects"],
        ["get_current_datetime", "get_weather", "list_notes", "list_projects"],
        eval_types=["classify", "tools"],
    ),
]


def _build_tags(case: EvalCase) -> list[str]:
    """Build tag list for a test case."""
    tags = [f"section:{case.section}", f"lang:{case.language}"]
    for et in case.eval_types:
        tags.append(f"level:{et}")
    return tags


def _build_metadata(case: EvalCase) -> dict:
    """Build metadata dict for a test case."""
    return {
        "source": "seed",
        "expected_categories": case.expected_categories,
        "expected_tools": case.expected_tools,
        "section": case.section,
        "eval_types": case.eval_types,
    }


async def _seed(db_path: str, clear: bool, dry_run: bool, section: str | None) -> None:
    cases = CASES
    if section:
        cases = [c for c in cases if c.section == section]
        if not cases:
            sections = sorted({c.section for c in CASES})
            print(f"No cases for section '{section}'. Available: {', '.join(sections)}")
            return

    if dry_run:
        print(f"[DRY RUN] Would process {len(cases)} test cases:\n")
        for i, c in enumerate(cases, 1):
            print(f"  {i:3d}. [{c.section}] {c.input_text[:70]}")
            print(f"       categories={c.expected_categories}  tools={c.expected_tools}")
        print(f"\nTotal: {len(cases)} cases")
        return

    conn, _ = await init_db(db_path)

    try:
        if clear:
            # Only delete seed entries — preserve organic ones
            cursor = await conn.execute(
                'SELECT id FROM eval_dataset WHERE metadata LIKE \'%"source": "seed"%\''
            )
            seed_ids = [r[0] for r in await cursor.fetchall()]
            if seed_ids:
                placeholders = ",".join("?" * len(seed_ids))
                await conn.execute(
                    f"DELETE FROM eval_dataset_tags WHERE dataset_id IN ({placeholders})",
                    seed_ids,
                )
                await conn.execute(
                    f"DELETE FROM eval_dataset WHERE id IN ({placeholders})",
                    seed_ids,
                )
                await conn.commit()
                print(f"Cleared {len(seed_ids)} seed entries.")
            else:
                print("No seed entries to clear.")

        # Fetch existing seed input_texts for idempotency
        cursor = await conn.execute(
            'SELECT input_text FROM eval_dataset WHERE metadata LIKE \'%"source": "seed"%\''
        )
        existing = {r[0] for r in await cursor.fetchall()}

        inserted = 0
        skipped = 0
        for case in cases:
            if case.input_text in existing:
                skipped += 1
                continue

            metadata = _build_metadata(case)
            tags = _build_tags(case)
            meta_json = json.dumps(metadata)

            cursor = await conn.execute(
                "INSERT INTO eval_dataset "
                "(trace_id, entry_type, input_text, output_text, expected_output, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (None, "golden", case.input_text, None, case.expected_output, meta_json),
            )
            dataset_id = cursor.lastrowid
            if tags and dataset_id:
                await conn.executemany(
                    "INSERT OR IGNORE INTO eval_dataset_tags (dataset_id, tag) VALUES (?, ?)",
                    [(dataset_id, tag) for tag in tags],
                )
            inserted += 1

        await conn.commit()
        print(
            f"Inserted: {inserted}, Skipped (already exist): {skipped}, Total cases: {len(cases)}"
        )
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed eval_dataset with golden test cases from the manual testing guide.",
    )
    parser.add_argument("--db", default="data/localforge.db", help="Path to SQLite database")
    parser.add_argument("--clear", action="store_true", help="Remove seed entries before inserting")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be inserted")
    parser.add_argument("--section", help="Only seed a specific section (e.g., math, time)")
    args = parser.parse_args()

    asyncio.run(
        _seed(db_path=args.db, clear=args.clear, dry_run=args.dry_run, section=args.section)
    )


if __name__ == "__main__":
    main()
