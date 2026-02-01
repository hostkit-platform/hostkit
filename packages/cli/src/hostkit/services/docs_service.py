"""Documentation service for HostKit.

Provides semantic search over HostKit documentation using:
- Vector service for embeddings and search
- Chatbot LLM providers for RAG answers

Uses a system-level "_hostkit" project to store the docs collection.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db


class DocsServiceError(Exception):
    """Docs service error with structured info."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


@dataclass
class DocChunk:
    """A chunk of documentation for indexing."""

    id: str
    title: str
    content: str
    section: str
    source: str
    chunk_type: str  # "service", "command", "concept", "example"


class DocsService:
    """Service for indexing and querying HostKit documentation."""

    # System project for docs - uses underscore prefix to indicate internal
    SYSTEM_PROJECT = "_hostkit"
    COLLECTION_NAME = "docs"

    def __init__(self):
        self.config = get_config()
        self.db = get_db()

    def _ensure_system_project(self) -> None:
        """Ensure the _hostkit system project exists for docs storage."""
        project = self.db.get_project(self.SYSTEM_PROJECT)
        if not project:
            # Create minimal system project record
            self.db.create_project(
                name=self.SYSTEM_PROJECT,
                runtime="python",
                port=0,  # No actual service
            )

    def _get_claude_md_path(self) -> Path:
        """Get path to CLAUDE.md."""
        # Check VPS location first
        vps_path = Path("/var/lib/hostkit/CLAUDE.md")
        if vps_path.exists():
            return vps_path

        # Check local development path
        local_path = self.config.data_dir.parent / "CLAUDE.md"
        if local_path.exists():
            return local_path

        # Try relative to package
        pkg_path = Path(__file__).parent.parent.parent.parent / "CLAUDE.md"
        if pkg_path.exists():
            return pkg_path

        raise DocsServiceError(
            code="CLAUDE_MD_NOT_FOUND",
            message="Could not find CLAUDE.md",
            suggestion="Ensure CLAUDE.md exists in /var/lib/hostkit/ or project root",
        )

    def _chunk_claude_md(self, content: str) -> list[DocChunk]:
        """Parse CLAUDE.md into semantic chunks by section."""
        chunks: list[DocChunk] = []

        # Split by ## and ### headers
        # Pattern: capture header level, title, and content until next header
        pattern = r"^(#{2,3})\s+(.+?)$\n(.*?)(?=^#{2,3}\s|\Z)"
        matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)

        for i, (level, title, body) in enumerate(matches):
            # Skip empty sections
            body = body.strip()
            if not body or len(body) < 50:
                continue

            # Determine chunk type from title/content
            title_lower = title.lower()
            if "service" in title_lower:
                chunk_type = "service"
            elif "command" in title_lower or "```bash" in body[:200]:
                chunk_type = "command"
            elif "example" in title_lower:
                chunk_type = "example"
            else:
                chunk_type = "concept"

            # Determine parent section (## level)
            section = title if level == "##" else self._find_parent_section(content, title)

            # Create chunk ID
            chunk_id = f"claude-md-{i:03d}-{self._slugify(title)}"

            # Truncate very long sections into sub-chunks
            if len(body) > 3000:
                sub_chunks = self._split_large_section(chunk_id, title, body, section, chunk_type)
                chunks.extend(sub_chunks)
            else:
                chunks.append(
                    DocChunk(
                        id=chunk_id,
                        title=title,
                        content=f"# {title}\n\n{body}",
                        section=section,
                        source="CLAUDE.md",
                        chunk_type=chunk_type,
                    )
                )

        return chunks

    def _split_large_section(
        self,
        base_id: str,
        title: str,
        body: str,
        section: str,
        chunk_type: str,
    ) -> list[DocChunk]:
        """Split large sections into smaller chunks."""
        chunks: list[DocChunk] = []

        # Split by paragraph or code blocks
        parts = re.split(r"\n\n+", body)
        current_chunk = ""
        chunk_num = 0

        for part in parts:
            if len(current_chunk) + len(part) > 2500:
                if current_chunk:
                    chunks.append(
                        DocChunk(
                            id=f"{base_id}-{chunk_num}",
                            title=f"{title} (part {chunk_num + 1})",
                            content=f"# {title}\n\n{current_chunk}",
                            section=section,
                            source="CLAUDE.md",
                            chunk_type=chunk_type,
                        )
                    )
                    chunk_num += 1
                current_chunk = part
            else:
                current_chunk = f"{current_chunk}\n\n{part}" if current_chunk else part

        # Don't forget the last chunk
        if current_chunk:
            chunks.append(
                DocChunk(
                    id=f"{base_id}-{chunk_num}",
                    title=f"{title} (part {chunk_num + 1})" if chunk_num > 0 else title,
                    content=f"# {title}\n\n{current_chunk}",
                    section=section,
                    source="CLAUDE.md",
                    chunk_type=chunk_type,
                )
            )

        return chunks

    def _find_parent_section(self, content: str, subsection_title: str) -> str:
        """Find the parent ## section for a ### subsection."""
        # Find position of subsection
        pos = content.find(f"### {subsection_title}")
        if pos == -1:
            return "General"

        # Look backwards for ## header
        before = content[:pos]
        match = re.search(r"^##\s+(.+?)$", before, re.MULTILINE)
        if match:
            # Get the last ## match before our position
            matches = list(re.finditer(r"^##\s+(.+?)$", before, re.MULTILINE))
            if matches:
                return matches[-1].group(1)

        return "General"

    def _slugify(self, text: str) -> str:
        """Convert text to URL-safe slug."""
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        return text.strip("-")[:50]

    def _chunk_capabilities(self) -> list[DocChunk]:
        """Generate chunks from capabilities command output."""
        chunks: list[DocChunk] = []

        try:
            # Run capabilities command to get structured data
            result = subprocess.run(
                ["hostkit", "--json", "capabilities"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return chunks

            data = json.loads(result.stdout)
            if not data.get("success"):
                return chunks

            caps = data.get("data", {})

            # Chunk services
            services = caps.get("services", {})
            for name, info in services.items():
                chunk_content = f"# {name.title()} Service\n\n"
                chunk_content += f"**Description**: {info.get('description', 'N/A')}\n\n"

                if info.get("env_vars"):
                    chunk_content += "**Environment Variables**:\n"
                    for var in info["env_vars"]:
                        chunk_content += f"- `{var}`\n"
                    chunk_content += "\n"

                if info.get("commands"):
                    chunk_content += "**Commands**:\n"
                    for cmd_name, cmd in info["commands"].items():
                        chunk_content += f"- `{cmd}` - {cmd_name}\n"

                chunks.append(
                    DocChunk(
                        id=f"service-{name}",
                        title=f"{name.title()} Service",
                        content=chunk_content,
                        section="Services",
                        source="capabilities",
                        chunk_type="service",
                    )
                )

            # Chunk command groups
            commands = caps.get("commands", {})
            for group_name, group_info in commands.items():
                if isinstance(group_info, dict) and group_info.get("commands"):
                    chunk_content = f"# hostkit {group_name}\n\n"
                    chunk_content += f"{group_info.get('help', '')}\n\n"
                    chunk_content += "**Subcommands**:\n"

                    for cmd in group_info.get("commands", []):
                        if isinstance(cmd, dict):
                            cmd_name = cmd.get("name", "")
                            cmd_help = cmd.get("help", "")
                            chunk_content += f"- `hostkit {group_name} {cmd_name}` - {cmd_help}\n"

                    chunks.append(
                        DocChunk(
                            id=f"cmd-{group_name}",
                            title=f"hostkit {group_name}",
                            content=chunk_content,
                            section="Commands",
                            source="capabilities",
                            chunk_type="command",
                        )
                    )

        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            # Capabilities not available, skip
            pass

        return chunks

    def index_docs(self, force: bool = False) -> dict[str, Any]:
        """Index CLAUDE.md and capabilities into vector store.

        Args:
            force: If True, recreate collection even if it exists

        Returns:
            Index statistics
        """
        from hostkit.services.vector_service import VectorService, VectorServiceError

        self._ensure_system_project()
        vector_service = VectorService()

        # Enable vector for system project if not already enabled
        try:
            # Try to list collections - if this fails, project isn't enabled
            vector_service.list_collections(self.SYSTEM_PROJECT)
        except VectorServiceError:
            # Enable vector for system project
            try:
                vector_service.enable_project(self.SYSTEM_PROJECT)
            except VectorServiceError as e:
                raise DocsServiceError(
                    code="VECTOR_SETUP_FAILED",
                    message=f"Could not enable vector service: {e.message}",
                    suggestion="Ensure vector service is running on the VPS",
                )

        # Delete existing collection if force
        if force:
            try:
                vector_service.delete_collection(self.SYSTEM_PROJECT, self.COLLECTION_NAME)
            except VectorServiceError:
                pass  # Collection may not exist

        # Create collection
        try:
            vector_service.create_collection(
                project=self.SYSTEM_PROJECT,
                name=self.COLLECTION_NAME,
                description="HostKit documentation for AI agent queries",
            )
        except VectorServiceError as e:
            if "already exists" not in e.message.lower():
                raise DocsServiceError(
                    code="COLLECTION_CREATE_FAILED",
                    message=f"Could not create collection: {e.message}",
                )

        # Parse and chunk CLAUDE.md
        claude_md_path = self._get_claude_md_path()
        claude_md_content = claude_md_path.read_text()
        claude_chunks = self._chunk_claude_md(claude_md_content)

        # Get capabilities chunks
        caps_chunks = self._chunk_capabilities()

        all_chunks = claude_chunks + caps_chunks

        # Ingest chunks
        ingested = 0
        errors = 0

        for chunk in all_chunks:
            try:
                # Format chunk with metadata for better retrieval
                formatted_content = f"""---
title: {chunk.title}
section: {chunk.section}
type: {chunk.chunk_type}
source: {chunk.source}
---

{chunk.content}
"""
                vector_service.ingest_text(
                    project=self.SYSTEM_PROJECT,
                    collection=self.COLLECTION_NAME,
                    content=formatted_content,
                    source_name=chunk.id,
                )
                ingested += 1
            except VectorServiceError:
                errors += 1

        # Store index metadata
        self._save_index_metadata(
            chunk_count=len(all_chunks),
            ingested=ingested,
            errors=errors,
        )

        return {
            "chunks_total": len(all_chunks),
            "chunks_ingested": ingested,
            "chunks_errors": errors,
            "sources": {
                "claude_md": len(claude_chunks),
                "capabilities": len(caps_chunks),
            },
            "indexed_at": datetime.utcnow().isoformat(),
        }

    def _save_index_metadata(self, chunk_count: int, ingested: int, errors: int) -> None:
        """Save index metadata for status command."""
        metadata_path = self.config.data_dir / "docs_index.json"
        metadata = {
            "chunk_count": chunk_count,
            "ingested": ingested,
            "errors": errors,
            "indexed_at": datetime.utcnow().isoformat(),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2))

    def get_index_status(self) -> dict[str, Any]:
        """Get documentation index status."""
        metadata_path = self.config.data_dir / "docs_index.json"

        if not metadata_path.exists():
            return {
                "indexed": False,
                "message": "Documentation not indexed yet",
                "suggestion": "Run 'hostkit docs index' to create the index",
            }

        metadata = json.loads(metadata_path.read_text())

        # Check if collection exists
        from hostkit.services.vector_service import VectorService, VectorServiceError

        vector_service = VectorService()

        try:
            collection_info = vector_service.get_collection_info(
                self.SYSTEM_PROJECT,
                self.COLLECTION_NAME,
            )
            document_count = collection_info.get("document_count", 0)
        except VectorServiceError:
            document_count = 0

        return {
            "indexed": True,
            "chunk_count": metadata.get("chunk_count", 0),
            "document_count": document_count,
            "indexed_at": metadata.get("indexed_at"),
            "collection": self.COLLECTION_NAME,
        }

    def query(
        self,
        question: str,
        limit: int = 5,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Query documentation with natural language.

        Args:
            question: Natural language question
            limit: Number of chunks to retrieve
            raw: If True, return raw chunks without LLM processing

        Returns:
            Answer with relevant commands and documentation
        """
        from hostkit.services.vector_service import VectorService, VectorServiceError

        vector_service = VectorService()

        # Search for relevant chunks
        try:
            results = vector_service.search(
                project=self.SYSTEM_PROJECT,
                collection=self.COLLECTION_NAME,
                query=question,
                limit=limit,
                threshold=0.3,  # Lower threshold for better recall
            )
        except VectorServiceError as e:
            raise DocsServiceError(
                code="SEARCH_FAILED",
                message=f"Search failed: {e.message}",
                suggestion="Ensure docs are indexed with 'hostkit docs index'",
            )

        chunks = results.get("results", [])

        if not chunks:
            return {
                "answer": "No relevant documentation found for your question.",
                "chunks": [],
                "commands": [],
            }

        # If raw mode, just return chunks
        if raw:
            return {
                "chunks": [
                    {
                        "content": c.get("content", ""),
                        "score": c.get("score", 0),
                        "source": c.get("metadata", {}).get("source_name", ""),
                    }
                    for c in chunks
                ],
            }

        # Use LLM for RAG answer
        answer = self._generate_rag_answer(question, chunks)

        return answer

    def _generate_rag_answer(self, question: str, chunks: list[dict]) -> dict[str, Any]:
        """Generate answer using LLM with retrieved chunks."""
        # Build context from chunks
        context_parts = []
        for i, chunk in enumerate(chunks):
            content = chunk.get("content", "")
            # Extract just the content after YAML frontmatter
            if "---" in content:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            context_parts.append(f"[Doc {i + 1}]\n{content}\n")

        context = "\n".join(context_parts)

        # Build RAG prompt
        system_prompt = (
            "You are a HostKit documentation assistant."
            " Answer questions about HostKit using ONLY the"
            " provided documentation context.\n\nRules:\n"
            "1. Be concise and direct\n"
            "2. Include specific commands when relevant"
            " (use exact syntax from docs)\n"
            "3. If the docs don't contain the answer, say so\n"
            "4. Format commands in backticks\n"
            "5. Return a JSON object with: answer, commands"
            " (list of relevant commands), see_also"
            " (related topics)"
        )

        user_prompt = f"""Documentation context:
{context}

Question: {question}

Respond with a JSON object containing:
- "answer": Your concise answer
- "commands": List of relevant hostkit commands
- "see_also": List of related topics to explore"""

        # Try to use chatbot LLM provider
        try:
            import anthropic

            # Get API key from llm.ini
            llm_config_path = Path("/etc/hostkit/llm.ini")
            if llm_config_path.exists():
                import configparser

                config = configparser.ConfigParser()
                config.read(llm_config_path)
                api_key = config.get("anthropic", "api_key", fallback=None)
            else:
                api_key = None

            if not api_key:
                # Fallback: return chunks without LLM processing
                return self._fallback_answer(question, chunks)

            client = anthropic.Anthropic(api_key=api_key)

            response = client.messages.create(
                model="claude-3-5-haiku-20241022",  # Fast and cheap for RAG
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Parse response
            response_text = response.content[0].text

            # Try to extract JSON
            try:
                # Find JSON in response
                json_match = re.search(r"\{[\s\S]*\}", response_text)
                if json_match:
                    result = json.loads(json_match.group())
                    return {
                        "answer": result.get("answer", response_text),
                        "commands": result.get("commands", []),
                        "see_also": result.get("see_also", []),
                        "sources": len(chunks),
                    }
            except json.JSONDecodeError:
                pass

            # Fallback: return raw response
            return {
                "answer": response_text,
                "commands": self._extract_commands(response_text),
                "see_also": [],
                "sources": len(chunks),
            }

        except Exception:
            # LLM not available, return chunks-based answer
            return self._fallback_answer(question, chunks)

    def _fallback_answer(self, question: str, chunks: list[dict]) -> dict[str, Any]:
        """Generate answer without LLM - extract key info from chunks."""
        # Extract commands from chunks
        commands = []
        content_parts = []

        for chunk in chunks:
            content = chunk.get("content", "")
            content_parts.append(content)

            # Extract commands (hostkit ...)
            cmd_matches = re.findall(r"`(hostkit [^`]+)`", content)
            commands.extend(cmd_matches)

        # Dedupe commands
        commands = list(dict.fromkeys(commands))[:5]

        # Build simple answer from first chunk
        first_content = chunks[0].get("content", "") if chunks else ""
        # Get first paragraph after frontmatter
        if "---" in first_content:
            parts = first_content.split("---", 2)
            if len(parts) >= 3:
                first_content = parts[2].strip()

        # Take first ~500 chars
        answer = first_content[:500]
        if len(first_content) > 500:
            answer += "..."

        return {
            "answer": answer,
            "commands": commands,
            "see_also": [],
            "sources": len(chunks),
            "note": "LLM unavailable - showing raw documentation excerpts",
        }

    def _extract_commands(self, text: str) -> list[str]:
        """Extract hostkit commands from text."""
        matches = re.findall(r"`(hostkit [^`]+)`", text)
        return list(dict.fromkeys(matches))[:10]
