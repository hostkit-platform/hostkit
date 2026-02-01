"""Image generation CLI commands for HostKit.

Provides AI image generation via Black Forest Labs Flux API.
System-wide API key, per-project rate limiting and usage tracking.
"""

import os
import time

import click
import requests

from hostkit.access import project_access
from hostkit.database import get_db
from hostkit.output import OutputFormatter
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register image service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="image",
        description=(
            "AI image generation via Black Forest Labs Flux API (flux-1.1-pro, flux-1.1-pro-ultra)"
        ),
        provision_flag=None,  # No auto-provisioning, uses system API key
        enable_command=None,  # Available to all projects by default
        env_vars_provided=["BFL_API_KEY"],  # System-level only
        related_commands=[
            "image generate",
            "image models",
            "image usage",
            "image history",
            "image config",
        ],
    )
)


# Flux model endpoints and constraints
# Based on BFL API OpenAPI spec
MODEL_ENDPOINTS = {
    # FLUX 1.1 - must be multiple of 32, range 256-1440
    "flux-1.1-pro": "https://api.bfl.ai/v1/flux-pro-1.1",
    # FLUX 1.1 Ultra - uses aspect_ratio instead of width/height
    "flux-1.1-pro-ultra": "https://api.bfl.ai/v1/flux-pro-1.1-ultra",
}

# Model constraints from BFL API docs
MODEL_CONSTRAINTS = {
    "flux-1.1-pro": {
        "min": 256,
        "max": 1440,
        "multiple": 32,
        "uses_aspect_ratio": False,
        "description": "Stable, dimensions must be multiple of 32 (256-1440)",
    },
    "flux-1.1-pro-ultra": {
        "min": 256,
        "max": 1440,
        "multiple": 1,
        "uses_aspect_ratio": True,
        "default_aspect": "16:9",
        "description": "Ultra quality, uses aspect ratio (21:9 to 9:21)",
    },
}

# Valid aspect ratios for ultra model
VALID_ASPECT_RATIOS = [
    "21:9",
    "16:9",
    "3:2",
    "4:3",
    "1:1",
    "3:4",
    "2:3",
    "9:16",
    "9:21",
]

# Approximate cost per image (for tracking)
MODEL_COSTS = {
    "flux-1.1-pro": 0.04,
    "flux-1.1-pro-ultra": 0.06,
}

# Default rate limits
DEFAULT_DAILY_LIMIT = 500
DEFAULT_HOURLY_LIMIT = 100


class ImageServiceError(Exception):
    """Image service error with structured info."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion


def get_api_key() -> str:
    """Get the BFL API key from environment or config."""
    key = os.environ.get("BFL_API_KEY")
    if key:
        return key

    # Try loading from config file
    config_path = "/etc/hostkit/config.yaml"
    if os.path.exists(config_path):
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
            key = config.get("bfl_api_key")
            if key:
                return key

    raise ImageServiceError(
        code="API_KEY_MISSING",
        message="BFL API key not configured",
        suggestion="Set BFL_API_KEY in /etc/hostkit/config.yaml",
    )


def check_rate_limit(project: str) -> None:
    """Check if project is within rate limits."""
    import getpass

    # ai-operator has unlimited access
    current_user = getpass.getuser()
    if current_user == "ai-operator" or current_user == "root":
        return

    db = get_db()

    # Get usage counts
    with db.connection() as conn:
        hourly = conn.execute(
            """
            SELECT COUNT(*) FROM image_generations
            WHERE project = ? AND created_at > datetime('now', '-1 hour')
            """,
            (project,),
        ).fetchone()[0]

        daily = conn.execute(
            """
            SELECT COUNT(*) FROM image_generations
            WHERE project = ? AND created_at > datetime('now', '-1 day')
            """,
            (project,),
        ).fetchone()[0]

    if hourly >= DEFAULT_HOURLY_LIMIT:
        raise ImageServiceError(
            code="RATE_LIMIT_HOURLY",
            message=f"Hourly limit ({DEFAULT_HOURLY_LIMIT}) exceeded",
            suggestion="Wait before generating more images",
        )

    if daily >= DEFAULT_DAILY_LIMIT:
        raise ImageServiceError(
            code="RATE_LIMIT_DAILY",
            message=f"Daily limit ({DEFAULT_DAILY_LIMIT}) exceeded",
            suggestion="Limit resets at midnight UTC",
        )


def validate_dimensions(
    model: str,
    width: int | None,
    height: int | None,
    aspect_ratio: str | None,
) -> tuple[int | None, int | None, str | None]:
    """Validate and adjust dimensions based on model constraints."""
    constraints = MODEL_CONSTRAINTS.get(model)
    if not constraints:
        raise ImageServiceError(
            code="INVALID_MODEL",
            message=f"Unknown model: {model}",
            suggestion=f"Available models: {', '.join(MODEL_ENDPOINTS.keys())}",
        )

    # Models that use aspect ratio instead of dimensions
    if constraints["uses_aspect_ratio"]:
        if aspect_ratio:
            if aspect_ratio not in VALID_ASPECT_RATIOS:
                raise ImageServiceError(
                    code="INVALID_ASPECT_RATIO",
                    message=f"Invalid aspect ratio: {aspect_ratio}",
                    suggestion=f"Valid ratios: {', '.join(VALID_ASPECT_RATIOS)}",
                )
            return None, None, aspect_ratio
        # Use default aspect ratio
        return None, None, constraints.get("default_aspect", "16:9")

    # Models that use width/height
    if width is None:
        width = 1024
    if height is None:
        height = 1024

    min_dim = constraints["min"]
    max_dim = constraints["max"]
    multiple = constraints["multiple"]

    # Validate range
    if width < min_dim or width > max_dim:
        raise ImageServiceError(
            code="INVALID_WIDTH",
            message=f"Width {width} out of range for {model}",
            suggestion=f"Width must be {min_dim}-{max_dim}px",
        )
    if height < min_dim or height > max_dim:
        raise ImageServiceError(
            code="INVALID_HEIGHT",
            message=f"Height {height} out of range for {model}",
            suggestion=f"Height must be {min_dim}-{max_dim}px",
        )

    # Check multiple constraint
    if multiple > 1:
        if width % multiple != 0:
            # Auto-adjust to nearest valid value
            width = (width // multiple) * multiple
            if width < min_dim:
                width = min_dim
        if height % multiple != 0:
            height = (height // multiple) * multiple
            if height < min_dim:
                height = min_dim

    return width, height, None


def record_generation(
    project: str,
    model: str,
    prompt: str,
    width: int,
    height: int,
    image_url: str,
    cost: float,
    duration_ms: int,
) -> None:
    """Record an image generation in the database."""
    db = get_db()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO image_generations
            (project, model, prompt, width, height,
             image_url, cost, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (project, model, prompt[:500], width, height, image_url, cost, duration_ms),
        )


def generate_image(
    prompt: str,
    model: str = "flux-2-pro",
    width: int | None = None,
    height: int | None = None,
    aspect_ratio: str | None = None,
) -> tuple[str, int, int, int]:
    """
    Generate an image using the Flux API.

    Returns:
        Tuple of (image_url, duration_ms, actual_width, actual_height)
    """
    endpoint = MODEL_ENDPOINTS.get(model)
    if not endpoint:
        raise ImageServiceError(
            code="INVALID_MODEL",
            message=f"Unknown model: {model}",
            suggestion=f"Available models: {', '.join(MODEL_ENDPOINTS.keys())}",
        )

    # Validate and adjust dimensions
    width, height, aspect_ratio = validate_dimensions(model, width, height, aspect_ratio)

    api_key = get_api_key()
    start_time = time.time()

    # Build request payload based on model type
    payload: dict = {"prompt": prompt}
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio
        # Store approximate dimensions for tracking
        actual_width, actual_height = 1024, 1024  # Default for aspect ratio models
    else:
        payload["width"] = width
        payload["height"] = height
        actual_width, actual_height = width, height

    # Submit generation request
    try:
        response = requests.post(
            endpoint,
            headers={
                "accept": "application/json",
                "x-key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise ImageServiceError(
            code="API_REQUEST_FAILED",
            message=f"Failed to submit request: {e}",
            suggestion="Check network connectivity and API key",
        )

    if "id" not in data:
        raise ImageServiceError(
            code="API_RESPONSE_INVALID",
            message="No request ID in response",
            suggestion="BFL API may be experiencing issues",
        )

    request_id = data["id"]
    polling_url = data.get("polling_url", f"https://api.bfl.ai/v1/get_result?id={request_id}")

    # Poll for result
    max_attempts = 120  # 60 seconds max
    for _ in range(max_attempts):
        time.sleep(0.5)

        try:
            result = requests.get(
                polling_url,
                headers={
                    "accept": "application/json",
                    "x-key": api_key,
                },
                timeout=10,
            ).json()
        except requests.RequestException:
            continue  # Retry on network errors

        status = result.get("status")

        if status == "Ready":
            duration_ms = int((time.time() - start_time) * 1000)
            image_url = result.get("result", {}).get("sample")
            if not image_url:
                raise ImageServiceError(
                    code="NO_IMAGE_URL",
                    message="Generation succeeded but no image URL returned",
                )
            return image_url, duration_ms, actual_width, actual_height

        if status in ("Error", "Failed"):
            raise ImageServiceError(
                code="GENERATION_FAILED",
                message=f"Image generation failed: {result.get('error', 'Unknown error')}",
            )

    raise ImageServiceError(
        code="GENERATION_TIMEOUT",
        message="Image generation timed out after 60 seconds",
        suggestion="Try again or use a faster model like flux-2-flex",
    )


@click.group()
@click.pass_context
def image(ctx: click.Context) -> None:
    """AI image generation service.

    Generate images using Black Forest Labs Flux models.
    Per-project rate limiting and usage tracking.
    """
    pass


@image.command("generate")
@click.argument("project")
@click.argument("prompt")
@click.option("--model", "-m", default="flux-1.1-pro", help="Model to use")
@click.option(
    "--width",
    "-w",
    default=None,
    type=int,
    help="Image width (default: 1024)",
)
@click.option(
    "--height",
    "-h",
    default=None,
    type=int,
    help="Image height (default: 1024)",
)
@click.option(
    "--aspect-ratio",
    "-a",
    default=None,
    help="Aspect ratio for ultra model (e.g., 16:9)",
)
@click.pass_context
@project_access("project")
def image_generate(
    ctx: click.Context,
    project: str,
    prompt: str,
    model: str,
    width: int | None,
    height: int | None,
    aspect_ratio: str | None,
) -> None:
    """Generate an image from a text prompt.

    Returns the URL of the generated image.

    Examples:
        hostkit image generate myapp "A futuristic city at sunset"
        hostkit image generate myapp "A cat" --width 512 --height 512
        hostkit image generate myapp "A landscape" --model flux-1.1-pro-ultra --aspect-ratio 16:9

    Model constraints:
        flux-1.1-pro: 256-1440px, must be multiple of 32 (default)
        flux-1.1-pro-ultra: uses --aspect-ratio instead of width/height
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    try:
        # Check rate limits
        check_rate_limit(project)

        # Generate image
        image_url, duration_ms, actual_width, actual_height = generate_image(
            prompt, model, width, height, aspect_ratio
        )

        # Record in database
        cost = MODEL_COSTS.get(model, 0.05)
        record_generation(
            project, model, prompt, actual_width, actual_height, image_url, cost, duration_ms
        )

        result_data = {
            "url": image_url,
            "model": model,
            "cost": cost,
            "duration_ms": duration_ms,
        }
        if aspect_ratio or MODEL_CONSTRAINTS.get(model, {}).get("uses_aspect_ratio"):
            result_data["aspect_ratio"] = aspect_ratio or MODEL_CONSTRAINTS[model].get(
                "default_aspect"
            )
        else:
            result_data["width"] = actual_width
            result_data["height"] = actual_height

        formatter.success(message="Image generated", data=result_data)

    except ImageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@image.command("models")
@click.pass_context
def image_models(ctx: click.Context) -> None:
    """List available image generation models.

    Example:
        hostkit image models
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    models = []
    for name in MODEL_ENDPOINTS.keys():
        constraints = MODEL_CONSTRAINTS.get(name, {})
        model_info = {
            "name": name,
            "cost": MODEL_COSTS.get(name, 0.05),
            "description": constraints.get("description", ""),
        }
        if constraints.get("uses_aspect_ratio"):
            model_info["mode"] = "aspect_ratio"
            model_info["valid_ratios"] = VALID_ASPECT_RATIOS
        else:
            model_info["mode"] = "dimensions"
            model_info["min"] = constraints.get("min", 64)
            model_info["max"] = constraints.get("max", 2048)
            model_info["multiple"] = constraints.get("multiple", 1)
        models.append(model_info)

    if ctx.obj["json_mode"]:
        formatter.success(
            message="Available models",
            data={"models": models, "valid_aspect_ratios": VALID_ASPECT_RATIOS},
        )
    else:
        click.echo("\nAvailable Models")
        click.echo("-" * 80)
        click.echo(f"{'MODEL':<20} {'COST':<8} {'CONSTRAINTS':<50}")
        click.echo("-" * 80)
        for m in models:
            cost_str = f"${m['cost']:.2f}"
            click.echo(f"{m['name']:<20} {cost_str:<8} {m['description']:<50}")
        click.echo("-" * 80)
        click.echo("\nValid aspect ratios: " + ", ".join(VALID_ASPECT_RATIOS))
        click.echo("Default: flux-1.1-pro")


@image.command("usage")
@click.argument("project")
@click.pass_context
@project_access("project")
def image_usage(ctx: click.Context, project: str) -> None:
    """Show image generation usage for a project.

    Example:
        hostkit image usage myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    # Get usage stats
    with db.connection() as conn:
        hourly = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM image_generations
            WHERE project = ? AND created_at > datetime('now', '-1 hour')
            """,
            (project,),
        ).fetchone()

        daily = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM image_generations
            WHERE project = ? AND created_at > datetime('now', '-1 day')
            """,
            (project,),
        ).fetchone()

        total = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM image_generations
            WHERE project = ?
            """,
            (project,),
        ).fetchone()

    data = {
        "hourly_count": hourly[0],
        "hourly_cost": round(hourly[1], 2),
        "hourly_limit": DEFAULT_HOURLY_LIMIT,
        "daily_count": daily[0],
        "daily_cost": round(daily[1], 2),
        "daily_limit": DEFAULT_DAILY_LIMIT,
        "total_count": total[0],
        "total_cost": round(total[1], 2),
    }

    if ctx.obj["json_mode"]:
        formatter.success(message=f"Image usage for '{project}'", data=data)
    else:
        click.echo(f"\nImage Usage: {project}")
        click.echo("-" * 40)
        click.echo(f"  Hourly:  {hourly[0]}/{DEFAULT_HOURLY_LIMIT} (${hourly[1]:.2f})")
        click.echo(f"  Daily:   {daily[0]}/{DEFAULT_DAILY_LIMIT} (${daily[1]:.2f})")
        click.echo(f"  Total:   {total[0]} images (${total[1]:.2f})")


@image.command("history")
@click.argument("project")
@click.option("--limit", "-n", default=10, help="Number of recent generations")
@click.pass_context
@project_access("project")
def image_history(ctx: click.Context, project: str, limit: int) -> None:
    """Show recent image generations for a project.

    Example:
        hostkit image history myapp
        hostkit image history myapp --limit 20
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT model, prompt, width, height, image_url, cost, duration_ms, created_at
            FROM image_generations
            WHERE project = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project, limit),
        ).fetchall()

    generations = [
        {
            "model": row[0],
            "prompt": row[1][:50] + "..." if len(row[1]) > 50 else row[1],
            "size": f"{row[2]}x{row[3]}",
            "url": row[4],
            "cost": row[5],
            "duration_ms": row[6],
            "created_at": row[7],
        }
        for row in rows
    ]

    if ctx.obj["json_mode"]:
        formatter.success(
            message=f"Recent generations for '{project}'", data={"generations": generations}
        )
    else:
        if not generations:
            click.echo("\nNo image generations found")
            return

        click.echo(f"\nRecent Generations: {project}")
        click.echo("-" * 80)
        for g in generations:
            click.echo(f"  [{g['created_at'][:16]}] {g['model']} {g['size']}")
            click.echo(f"    Prompt: {g['prompt']}")
            click.echo(f"    URL: {g['url']}")
            click.echo()


@image.command("config")
@click.option("--set-key", help="Set the BFL API key (root only)")
@click.pass_context
def image_config(ctx: click.Context, set_key: str | None) -> None:
    """View or configure image service settings.

    Example:
        hostkit image config
        hostkit image config --set-key YOUR_API_KEY
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    if set_key:
        # Setting key requires root
        from hostkit.access import require_root

        try:
            require_root()
        except Exception:
            formatter.error(
                code="ACCESS_DENIED",
                message="Setting API key requires root",
                suggestion="Run as root: sudo hostkit image config --set-key ...",
            )
            raise SystemExit(1)

        # Update config file
        config_path = "/etc/hostkit/config.yaml"
        import yaml

        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}

        config["bfl_api_key"] = set_key

        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        formatter.success(
            message="BFL API key configured",
            data={"key_prefix": set_key[:8] + "..."},
        )
    else:
        # Show current config
        try:
            key = get_api_key()
            key_status = f"{key[:8]}...{key[-4:]}"
        except ImageServiceError:
            key_status = "Not configured"

        data = {
            "api_key": key_status,
            "models": list(MODEL_ENDPOINTS.keys()),
            "default_model": "flux-1.1-pro",
            "hourly_limit": DEFAULT_HOURLY_LIMIT,
            "daily_limit": DEFAULT_DAILY_LIMIT,
        }

        if ctx.obj["json_mode"]:
            formatter.success(message="Image service configuration", data=data)
        else:
            click.echo("\nImage Service Configuration")
            click.echo("-" * 40)
            click.echo(f"  API Key:       {key_status}")
            click.echo("  Default Model: flux-1.1-pro")
            click.echo(f"  Hourly Limit:  {DEFAULT_HOURLY_LIMIT} images")
            click.echo(f"  Daily Limit:   {DEFAULT_DAILY_LIMIT} images")
            click.echo(f"  Models:        {len(MODEL_ENDPOINTS)}")
