#!/usr/bin/env python3
"""Smart Standalone Pantheon Toolset Generator with Template Files"""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

# Import Pantheon components
sys.path.append(str(Path(__file__).parent.parent))
from pantheon.agent import Agent
from pantheon.toolsets.file_editor import FileEditorToolSet
from pantheon.toolsets.python import PythonInterpreterToolSet

console = Console()


class SmartStandaloneGenerator:
    """Smart standalone toolset generator using template files"""

    def __init__(self):
        self.console = console
        self.agent = None
        self.workspace = Path("./ext_toolsets")
        self.workspace.mkdir(exist_ok=True)

    async def initialize_agent(self, model: str = "gpt-4o-mini"):
        """Initialize the generation agent with template files"""

        # Load all template files
        knowledge_base = self._load_template_files()

        generation_instructions = f"""You are a Smart Pantheon External Toolset Generator with complete template-based knowledge.

COMPLETE KNOWLEDGE BASE:
{knowledge_base}

YOUR APPROACH:
1. 📄 **Template-Based**: Use the customized TOOLSET_GENERATION_TEMPLATE.md that has been pre-filled with domain-specific optimizations
2. 🎯 **Domain-Specific**: The template includes specialized patterns for different domains
3. 🔧 **Production-Ready**: Generate complete, working implementations following the template guidance
4. ✅ **Quality Assured**: Follow all design patterns and integration requirements from the loaded documents

GENERATION PROCESS:
When asked to generate a toolset:
1. Use the pre-customized template that includes domain-specific optimizations
2. Follow the design patterns from EXTERNAL_TOOLSET_DESIGN.md
3. Implement Agent integration per EXTERNAL_TOOLSET_AGENT_INTEGRATION.md  
4. Create production-quality code with comprehensive error handling
5. Generate complete file structure with documentation

The template files provide complete guidance for creating professional-grade external toolsets."""

        # Initialize agent with tools
        self.agent = Agent(
            "smart_toolset_generator", generation_instructions, model=model
        )

        # Add file editing capabilities
        file_editor = FileEditorToolSet("file_editor", workspace_path=self.workspace)
        python_interpreter = PythonInterpreterToolSet(
            "python_interpreter", workdir=str(self.workspace)
        )

        self.agent.toolset(file_editor)
        self.agent.toolset(python_interpreter)

        console.print("[green]✅ Smart standalone generator initialized[/green]")

    def _load_template_files(self) -> str:
        """Load all template files from local directory"""

        knowledge_base = "# SMART PANTHEON TOOLSET GENERATION WITH TEMPLATE FILES\n\n"

        # Load all template files
        template_files = [
            "TOOLSET_GENERATION_TEMPLATE.md",
            "EXTERNAL_TOOLSET_DESIGN.md",
            "EXTERNAL_TOOLSET_AGENT_INTEGRATION.md",
        ]

        for filename in template_files:
            file_path = Path(__file__).parent / filename
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    knowledge_base += f"## {filename}\n\n{content}\n\n---\n\n"
                    console.print(f"[dim]📄 Loaded {filename}[/dim]")
                except Exception as e:
                    console.print(
                        f"[yellow]Warning: Failed to load {filename}: {e}[/yellow]"
                    )
            else:
                console.print(f"[yellow]Warning: {filename} not found[/yellow]")

        return knowledge_base

    def _customize_template_for_domain(
        self,
        name: str,
        domain: str,
        description: str,
        requirements: Optional[str] = None,
    ) -> str:
        """Customize the base template for specific domain and requirements"""

        # Load base template
        template_path = Path(__file__).parent / "TOOLSET_GENERATION_TEMPLATE.md"
        if not template_path.exists():
            raise FileNotFoundError("TOOLSET_GENERATION_TEMPLATE.md not found")

        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        # Domain-specific customizations
        domain_specs = {
            "web_scraper": {
                "primary_purpose": "provide intelligent web scraping with rate limiting, anti-bot detection, and content extraction",
                "main_tool": "requests + beautifulsoup4",
                "dependencies": "requests, beautifulsoup4, selenium, fake-useragent",
                "detect": "proxy servers, browser installations, existing sessions",
            },
            "api_client": {
                "primary_purpose": "provide robust REST API client with authentication, caching, and retry logic",
                "main_tool": "requests + httpx",
                "dependencies": "requests, httpx, pydantic, cachetools",
                "detect": "API keys, authentication tokens, cached responses",
            },
            "data_processor": {
                "primary_purpose": "provide comprehensive data processing with validation, transformation, and analysis",
                "main_tool": "pandas + numpy",
                "dependencies": "pandas, numpy, pydantic, polars",
                "detect": "data files, schema definitions, processing configurations",
            },
        }

        # Get domain spec or use defaults
        spec = domain_specs.get(
            domain,
            {
                "primary_purpose": f"provide {domain} capabilities",
                "main_tool": "python standard library",
                "dependencies": "",
                "detect": "configuration files",
            },
        )

        # Fill template variables
        customized = template_content.replace("[TOOLSET_NAME]", name)
        customized = customized.replace("[PRIMARY_PURPOSE]", spec["primary_purpose"])
        customized = customized.replace("[MAIN_TOOL_NAME]", spec["main_tool"])
        customized = customized.replace("[LIST_OF_DEPENDENCIES]", spec["dependencies"])
        customized = customized.replace("[WHAT_TO_DETECT]", spec["detect"])

        # Add user requirements if provided
        if requirements:
            customized += f"\n\n### Additional User Requirements:\n{requirements}\n"

        return customized

    async def generate_smart_toolset(
        self,
        name: str,
        domain: str,
        description: str,
        requirements: Optional[str] = None,
    ) -> bool:
        """Generate toolset using smart template-based approach"""

        if not self.agent:
            console.print(
                "[red]Agent not initialized. Call initialize_agent() first.[/red]"
            )
            return False

        # Domain validation removed - Smart Generator supports any domain through dynamic templates
        # The template system will automatically adapt to any domain

        console.print(
            Panel(
                f"[cyan]🧠 Starting Smart Template-Based Generation...[/cyan]\n"
                f"📦 Toolset: {name}\n"
                f"🎯 Domain: {domain}\n"
                f"📝 Description: {description}\n"
                f"📄 Template: Customized for {domain} with domain-specific optimizations",
                title="🚀 Smart Pantheon Generator",
                border_style="cyan",
            )
        )

        # Customize template for this specific request
        customized_template = self._customize_template_for_domain(
            name, domain, description, requirements
        )

        # Create smart generation prompt
        generation_prompt = f"""Generate a complete external toolset using the SMART TEMPLATE-BASED APPROACH:

TOOLSET SPECIFICATIONS:
- Name: {name}
- Domain: {domain}
- Description: {description}
{f"- Requirements: {requirements}" if requirements else ""}

SMART GENERATION APPROACH:
I have customized the TOOLSET_GENERATION_TEMPLATE.md specifically for this {domain} toolset:

CUSTOMIZED TEMPLATE:
{customized_template[:6000]}...

INSTRUCTIONS:
1. Follow the customized template exactly - it has been pre-filled with {domain} optimizations
2. Use the design patterns from EXTERNAL_TOOLSET_DESIGN.md
3. Implement Agent integration per EXTERNAL_TOOLSET_AGENT_INTEGRATION.md
4. Generate ALL required files: toolset.py, config.json, __init__.py, README.md
5. Make all code production-ready with comprehensive error handling

The template provides complete guidance for creating a professional {domain} toolset."""

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Smart template-based generation...", total=1)

                # Send smart generation request to agent
                response = await self.agent.run(generation_prompt)

                progress.update(
                    task, advance=1, description="Smart generation completed"
                )

            console.print("[green]✅ Smart template-based generation completed[/green]")

            # Test the generated toolset
            console.print("\n[cyan]🔍 Running automatic validation...[/cyan]")
            test_success = await self._test_toolset(name)

            if test_success:
                console.print(
                    Panel(
                        f"[green]🎉 Smart toolset '{name}' generated and validated![/green]\n\n"
                        f"📁 Location: {self.workspace / name}/\n"
                        f"🎯 Domain: {domain} with smart template customization\n"
                        f"📄 Template: Pre-filled with {domain}-specific optimizations\n"
                        f"🧪 Validation: All tests passed\n\n"
                        f"🚀 Next steps:\n"
                        f"1. Review smart implementation in {name}/ directory\n"
                        f"2. Install dependencies as specified\n"
                        f"3. Test: python -c \"from {name}.toolset import *; print('Success!')\"\n"
                        f"4. Use: python -m pantheon.cli --ext-toolsets {name}",
                        title="🏆 Smart Generation Success",
                        border_style="green",
                    )
                )
            else:
                console.print(
                    f"[yellow]⚠️ Toolset '{name}' generated but validation failed.[/yellow]"
                )

            return True

        except Exception as e:
            console.print(f"[red]❌ Smart generation failed: {str(e)}[/red]")
            return False

    async def _test_toolset(self, name: str) -> bool:
        """Test generated toolset"""
        try:
            test_code = f"""
import sys
sys.path.append('./ext_toolsets')
try:
    from {name}.toolset import *
    print("SUCCESS: Smart toolset loaded")
except Exception as e:
    print("ERROR:", str(e))
"""

            result = subprocess.run(
                [sys.executable, "-c", test_code],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and "SUCCESS" in result.stdout:
                console.print(
                    f"[green]✅ Smart toolset {name} validation passed[/green]"
                )
                return True
            else:
                console.print(f"[red]❌ Smart toolset {name} validation failed[/red]")
                return False

        except Exception as e:
            console.print(f"[red]❌ Validation error: {str(e)}[/red]")
            return False

    async def interactive_generation(self):
        """Interactive smart generation mode"""

        console.print(
            Panel(
                "[bold cyan]🧠 Smart Pantheon Toolset Generator[/bold cyan]\n"
                "Template-Based Generation with Domain Customization",
                border_style="cyan",
            )
        )

        # Show example domains (but any domain is supported)
        domains = {
            "web_scraper": "Smart web scraping with anti-bot detection",
            "api_client": "Production API client with multi-auth support",
            "data_processor": "Advanced data processing with validation",
            "database_client": "Enterprise database management",
            "file_manager": "Intelligent file operations",
            "image_processor": "Computer vision with batch processing",
            "text_processor": "NLP and text analysis tools",
            "system_monitor": "System monitoring with alerting",
            "automation": "Workflow automation with retry logic",
            "security": "Security tools with encryption",
            "bioinformatics": "Bioinformatics analysis and workflows",
            "machine_learning": "ML/AI model training and inference",
            "devops": "DevOps and infrastructure automation",
        }

        console.print("\n[bold]Available Smart Domains:[/bold]")
        table = Table()
        table.add_column("Domain", style="cyan")
        table.add_column("Description", style="white")

        for domain, desc in domains.items():
            table.add_row(domain, desc)

        console.print(table)
        console.print(
            "\n[dim]Note: You can use ANY domain name - these are just examples![/dim]"
        )

        # Get input
        name = Prompt.ask(
            "\nToolset name (lowercase, underscores)", default="my_toolset"
        )
        domain = Prompt.ask("Domain (use any name you want)", default="bioinformatics")
        description = Prompt.ask(
            "Description", default=domains.get(domain, f"Custom {domain} toolset")
        )

        has_requirements = Confirm.ask("Do you have specific requirements?")
        requirements = None
        if has_requirements:
            requirements = Prompt.ask("Describe specific requirements")

        # Show generation plan
        console.print(f"\n[bold]Smart Generation Plan:[/bold]")
        console.print(f"📦 Name: {name}")
        console.print(f"🎯 Domain: {domain}")
        console.print(f"📝 Description: {description}")
        console.print(f"📄 Template: Will be customized for {domain}")
        if requirements:
            console.print(f"🔧 Requirements: {requirements}")

        if Confirm.ask("\nProceed with smart template-based generation?"):
            success = await self.generate_smart_toolset(
                name, domain, description, requirements
            )

            if not success:
                console.print("[red]Generation failed.[/red]")
        else:
            console.print("[yellow]Generation cancelled.[/yellow]")


async def main():
    """Main CLI interface for smart standalone generator"""
    parser = argparse.ArgumentParser(
        description="Smart Standalone Pantheon Toolset Generator"
    )
    parser.add_argument("--name", help="Toolset name")
    parser.add_argument("--domain", help="Toolset domain")
    parser.add_argument("--description", help="Toolset description")
    parser.add_argument("--requirements", help="Additional requirements")
    parser.add_argument("--model", help="AI model to use", default="gpt-4o-mini")
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Interactive mode"
    )
    parser.add_argument(
        "--workspace", help="Output workspace directory", default="./ext_toolsets"
    )

    args = parser.parse_args()

    # Initialize smart generator
    generator = SmartStandaloneGenerator()
    generator.workspace = Path(args.workspace)
    generator.workspace.mkdir(exist_ok=True)

    # Initialize smart AI agent
    console.print("[cyan]🧠 Initializing Smart Generator with Template Files...[/cyan]")
    await generator.initialize_agent(args.model)

    if args.interactive or not (args.name and args.domain):
        # Interactive mode
        await generator.interactive_generation()
    else:
        # Direct generation mode
        success = await generator.generate_smart_toolset(
            args.name,
            args.domain,
            args.description or f"Smart {args.domain} toolset",
            args.requirements,
        )

        if success:
            console.print(
                f"[green]🎉 Smart toolset '{args.name}' generated successfully![/green]"
            )
        else:
            console.print(
                f"[red]❌ Failed to generate smart toolset '{args.name}'[/red]"
            )
            sys.exit(1)


def cli():
    """Smart CLI entry point"""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
