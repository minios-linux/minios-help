import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
COMPILER = (HERE.parent.parent / "minios-gui" / "tools" /
            "markdown-compiler.mjs")


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.output = self.root / "output"
        self.docs.mkdir()
        self.output.mkdir()
        self.mmdc = self.root / "fake-mmdc.py"
        self.mmdc.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "args=sys.argv[1:]\n"
            "config=pathlib.Path(args[args.index('-c')+1])\n"
            "assert json.loads(config.read_text())['handDrawnSeed'] == 1\n"
            "out=pathlib.Path(args[args.index('-o')+1])\n"
            "out.write_text('''<svg xmlns=\"http://www.w3.org/2000/svg\">"
            "<style>@keyframes dash{to{stroke-dashoffset:0;}}"
            ".node{filter:drop-shadow(1px 2px 2px rgba(0,0,0,1));"
            "animation:dash 5s linear infinite;stroke:#123456;}"
            ":root{--mermaid-font-family:sans-serif;}</style>"
            "<path class=\"node\" d=\"M0 0L1 1\"/></svg>''')\n",
            encoding="utf-8")
        self.mmdc.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def compile(self, source):
        source_path = self.docs / "page.md"
        source_path.write_text(source, encoding="utf-8")
        batch = {
            "schema_version": 1,
            "docs_root": str(self.docs),
            "output_root": str(self.output),
            "mermaid_command": str(self.mmdc),
            "items": [{
                "id": "page", "text": source,
                "source_path": str(source_path),
                "output_path": "en/page.json",
            }],
        }
        batch_path = self.root / "batch.json"
        batch_path.write_text(json.dumps(batch), encoding="utf-8")
        result = subprocess.run(
            ["node", str(COMPILER), str(batch_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        response = json.loads(result.stdout)
        document = json.loads(
            (self.output / "en" / "page.json").read_text(encoding="utf-8"))
        return document, response

    def test_gfm_and_extended_markdown_compile_to_ir(self):
        document, _response = self.compile(
            "# Title\n\n"
            "- [x] done\n- [ ] pending\n\n"
            "| Name | Value |\n| --- | --- |\n| **MiniOS** | `GTK3` |\n\n"
            "Term\n: Definition *here*\n\n"
            "Reference[^one]\n\n[^one]: Footnote text\n\n"
            "> [!NOTE]\n> Alert **body**\n\n"
            "!!! warning \"Careful\"\n    Admonition text\n\n"
            "H~2~O and x^2^\n")
        raw = json.dumps(document["nodes"], ensure_ascii=False)
        self.assertIn('"table"', raw)
        self.assertIn('"strong"', raw)
        self.assertIn('"code"', raw)
        self.assertIn('"item", true', raw)
        self.assertIn('"item", false', raw)
        self.assertIn('"subscript"', raw)
        self.assertIn('"superscript"', raw)
        self.assertIn('"admonition", "note"', raw)
        self.assertIn('"admonition", "warning", "Careful"', raw)
        self.assertIn("Footnote text", document["plain_text"])
        self.assertEqual(document["headings"][0]["anchor"], "title")

    def test_fenced_code_uses_language_for_syntax_without_rendering_label(self):
        document, _response = self.compile(
            "```bash\nif [ \"$mode\" = live ]; then\n  echo ready # status\nfi\n```\n")
        block = document["nodes"][0]
        self.assertEqual(block[0], "code_block")
        self.assertEqual(block[2], "bash")
        self.assertNotIn("bash\n", block[1])
        highlighted = block[3]
        self.assertEqual(
            "".join(token[1] for token in highlighted), block[1])
        self.assertTrue(all(token[0] == "syntax" for token in highlighted))
        self.assertTrue(all(len(token) == 4 for token in highlighted))
        self.assertIn("#D73A49", {token[2] for token in highlighted})
        self.assertIn("#F97583", {token[3] for token in highlighted})

    def test_mermaid_is_replaced_by_sanitized_svg_asset(self):
        document, response = self.compile(
            "# Diagram\n\n```mermaid\nflowchart LR\nA --> B\n```\n")
        diagrams = [node for node in document["nodes"] if node[0] == "diagram"]
        self.assertEqual(len(diagrams), 1)
        relative = diagrams[0][1]
        self.assertTrue(relative.startswith("assets/mermaid-"))
        svg = (self.output / relative).read_text(encoding="utf-8")
        self.assertNotIn("foreignObject", svg)
        self.assertNotIn("@keyframes", svg)
        self.assertNotIn("drop-shadow", svg)
        self.assertNotIn("animation:", svg)
        self.assertNotIn("--mermaid-font-family", svg)
        self.assertIn("stroke:#123456", svg)
        self.assertIn(relative, response["assets"])


if __name__ == "__main__":
    unittest.main()
