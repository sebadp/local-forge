"""Template registry and scaffolding for project workspaces.

Each template is a set of in-memory file definitions (no external template
directories needed). The scaffold function creates a workspace, writes the
template files, and runs git init.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.workspace.engine import WorkspaceEngine

logger = logging.getLogger(__name__)


# Template definitions: name -> {description, files: {relative_path: content_template}}
# Content templates support {PROJECT_NAME} and {DESCRIPTION} placeholders.
TEMPLATE_REGISTRY: dict[str, dict] = {
    "html-static": {
        "description": "Static HTML/CSS/JS website",
        "files": {
            "index.html": (
                '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                "  <title>{PROJECT_NAME}</title>\n"
                '  <link rel="stylesheet" href="styles.css">\n'
                "</head>\n<body>\n"
                "  <header>\n    <h1>{PROJECT_NAME}</h1>\n"
                "    <p>{DESCRIPTION}</p>\n  </header>\n"
                "  <main>\n    <!-- Content goes here -->\n  </main>\n"
                "  <footer>\n    <p>&copy; 2026 {PROJECT_NAME}</p>\n  </footer>\n"
                '  <script src="script.js"></script>\n'
                "</body>\n</html>"
            ),
            "styles.css": (
                "/* {PROJECT_NAME} styles */\n"
                "*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }\n"
                "body { font-family: system-ui, -apple-system, sans-serif; line-height: 1.6; "
                "color: #333; max-width: 1200px; margin: 0 auto; padding: 1rem; }\n"
                "header { text-align: center; padding: 2rem 0; }\n"
                "main { padding: 2rem 0; }\n"
                "footer { text-align: center; padding: 2rem 0; border-top: 1px solid #eee; }\n"
            ),
            "script.js": (
                "// {PROJECT_NAME}\n"
                "document.addEventListener('DOMContentLoaded', () => {\n"
                "  console.log('{PROJECT_NAME} loaded');\n"
                "});\n"
            ),
            "README.md": "# {PROJECT_NAME}\n\n{DESCRIPTION}\n",
        },
    },
    "python-fastapi": {
        "description": "Python FastAPI REST API with SQLite",
        "files": {
            "main.py": (
                '"""FastAPI application — {PROJECT_NAME}."""\n\n'
                "from fastapi import FastAPI\nfrom fastapi.middleware.cors import CORSMiddleware\n\n"
                'app = FastAPI(title="{PROJECT_NAME}", description="{DESCRIPTION}")\n\n'
                "app.add_middleware(\n    CORSMiddleware,\n"
                '    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],\n)\n\n\n'
                '@app.get("/health")\ndef health():\n    return {{"status": "ok"}}\n'
            ),
            "models.py": (
                '"""Pydantic models for {PROJECT_NAME}."""\n\n'
                "from pydantic import BaseModel\n\n\n"
                "class Item(BaseModel):\n"
                "    id: int | None = None\n"
                '    name: str\n    description: str = ""\n'
            ),
            "requirements.txt": "fastapi>=0.100\nuvicorn[standard]\npydantic>=2.0\naiosqlite\n",
            "tests/test_api.py": (
                "from fastapi.testclient import TestClient\n"
                "from main import app\n\n"
                "client = TestClient(app)\n\n\n"
                "def test_health():\n"
                '    r = client.get("/health")\n'
                "    assert r.status_code == 200\n"
                '    assert r.json()["status"] == "ok"\n'
            ),
            "README.md": "# {PROJECT_NAME}\n\n{DESCRIPTION}\n\n## Run\n\n```bash\npip install -r requirements.txt\nuvicorn main:app --reload\n```\n",
        },
    },
    "react-vite": {
        "description": "React + TypeScript with Vite",
        "files": {
            "package.json": (
                '{{\n  "name": "{PROJECT_NAME}",\n  "private": true,\n  "version": "0.1.0",\n'
                '  "type": "module",\n  "scripts": {{\n'
                '    "dev": "vite",\n    "build": "tsc && vite build",\n    "preview": "vite preview"\n'
                '  }},\n  "dependencies": {{\n    "react": "^18.3.0",\n    "react-dom": "^18.3.0"\n'
                '  }},\n  "devDependencies": {{\n'
                '    "@types/react": "^18.3.0",\n    "@types/react-dom": "^18.3.0",\n'
                '    "@vitejs/plugin-react": "^4.3.0",\n    "typescript": "^5.5.0",\n'
                '    "vite": "^5.4.0"\n  }}\n}}'
            ),
            "index.html": (
                '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
                '  <meta charset="UTF-8" />\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
                "  <title>{PROJECT_NAME}</title>\n</head>\n<body>\n"
                '  <div id="root"></div>\n'
                '  <script type="module" src="/src/main.tsx"></script>\n'
                "</body>\n</html>"
            ),
            "src/main.tsx": (
                "import React from 'react'\nimport ReactDOM from 'react-dom/client'\n"
                "import App from './App'\n\n"
                "ReactDOM.createRoot(document.getElementById('root')!).render(\n"
                "  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n)\n"
            ),
            "src/App.tsx": (
                "function App() {{\n  return (\n    <div>\n"
                "      <h1>{PROJECT_NAME}</h1>\n"
                "      <p>{DESCRIPTION}</p>\n"
                "    </div>\n  )\n}}\n\nexport default App\n"
            ),
            "vite.config.ts": (
                "import {{ defineConfig }} from 'vite'\nimport react from '@vitejs/plugin-react'\n\n"
                "export default defineConfig({{\n  plugins: [react()],\n}})\n"
            ),
            "tsconfig.json": (
                '{{\n  "compilerOptions": {{\n'
                '    "target": "ES2020",\n    "useDefineForClassFields": true,\n'
                '    "lib": ["ES2020", "DOM", "DOM.Iterable"],\n'
                '    "module": "ESNext",\n    "skipLibCheck": true,\n'
                '    "moduleResolution": "bundler",\n'
                '    "allowImportingTsExtensions": true,\n'
                '    "resolveJsonModule": true,\n    "isolatedModules": true,\n'
                '    "noEmit": true,\n    "jsx": "react-jsx",\n'
                '    "strict": true\n  }},\n  "include": ["src"]\n}}'
            ),
            "README.md": "# {PROJECT_NAME}\n\n{DESCRIPTION}\n\n## Run\n\n```bash\nnpm install\nnpm run dev\n```\n",
        },
    },
    "nextjs": {
        "description": "Next.js App Router project",
        "files": {
            "package.json": (
                '{{\n  "name": "{PROJECT_NAME}",\n  "version": "0.1.0",\n  "private": true,\n'
                '  "scripts": {{\n    "dev": "next dev",\n    "build": "next build",\n'
                '    "start": "next start"\n  }},\n'
                '  "dependencies": {{\n    "next": "^14.2.0",\n    "react": "^18.3.0",\n'
                '    "react-dom": "^18.3.0"\n  }},\n'
                '  "devDependencies": {{\n    "typescript": "^5.5.0",\n'
                '    "@types/react": "^18.3.0",\n    "@types/node": "^20.0.0"\n  }}\n}}'
            ),
            "app/layout.tsx": (
                "export const metadata = {{\n  title: '{PROJECT_NAME}',\n"
                "  description: '{DESCRIPTION}',\n}}\n\n"
                "export default function RootLayout({{\n  children,\n}}: {{\n"
                "  children: React.ReactNode\n}}) {{\n  return (\n"
                '    <html lang="en">\n      <body>{{children}}</body>\n    </html>\n  )\n}}\n'
            ),
            "app/page.tsx": (
                "export default function Home() {{\n  return (\n    <main>\n"
                "      <h1>{PROJECT_NAME}</h1>\n"
                "      <p>{DESCRIPTION}</p>\n"
                "    </main>\n  )\n}}\n"
            ),
            "next.config.js": "/** @type {{import('next').NextConfig}} */\nconst nextConfig = {{}}\nmodule.exports = nextConfig\n",
            "tsconfig.json": (
                '{{\n  "compilerOptions": {{\n'
                '    "target": "es5",\n    "lib": ["dom", "dom.iterable", "esnext"],\n'
                '    "allowJs": true,\n    "skipLibCheck": true,\n    "strict": true,\n'
                '    "noEmit": true,\n    "esModuleInterop": true,\n'
                '    "module": "esnext",\n    "moduleResolution": "bundler",\n'
                '    "resolveJsonModule": true,\n    "isolatedModules": true,\n'
                '    "jsx": "preserve",\n    "incremental": true,\n'
                '    "plugins": [{{"name": "next"}}],\n'
                '    "paths": {{"@/*": ["./*"]}}\n  }},\n'
                '  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],\n'
                '  "exclude": ["node_modules"]\n}}'
            ),
            "README.md": "# {PROJECT_NAME}\n\n{DESCRIPTION}\n\n## Run\n\n```bash\nnpm install\nnpm run dev\n```\n",
        },
    },
}


def list_templates() -> list[dict]:
    """Return available templates with names and descriptions."""
    return [
        {"name": name, "description": t["description"], "files": list(t["files"].keys())}
        for name, t in TEMPLATE_REGISTRY.items()
    ]


def scaffold(
    engine: WorkspaceEngine,
    name: str,
    template_name: str,
    phone: str = "",
    description: str = "",
) -> tuple[Path, list[str]]:
    """Create a workspace and populate it with template files.

    Returns (workspace_path, list_of_created_files).
    """
    template = TEMPLATE_REGISTRY.get(template_name)
    if not template:
        available = ", ".join(TEMPLATE_REGISTRY.keys())
        raise ValueError(f"Unknown template: {template_name!r}. Available: {available}")

    workspace = engine.create_workspace(name, phone=phone)

    created: list[str] = []
    desc = description or f"A {template_name} project"
    for rel_path, content_template in template["files"].items():
        content = (
            content_template
            .replace("{PROJECT_NAME}", name)
            .replace("{DESCRIPTION}", desc)
            .replace("{{", "{")
            .replace("}}", "}")
        )
        file_path = workspace / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        created.append(rel_path)

    logger.info("workspace.scaffolded: %s with template %s (%d files)", name, template_name, len(created))
    return workspace, created
