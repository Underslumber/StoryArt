# Project model profiles

Switch the StoryArt project configuration with one command from the repository root:

```powershell
.\scripts\set-model-profile.ps1 5.6
```

```powershell
.\scripts\set-model-profile.ps1 6
```

The command updates only the root model, the default agent model, and their reasoning effort in `.codex/config.toml`. It preserves other project settings. If the project config does not exist, the script creates it from `config/codex.project.example.toml` first. The active profile is the model family shown by those settings: use the matching `-sol` or `-luna` model for role-specific assignments in project instructions.

| Profile | Root default | Default agent |
|---|---|---|
| `5.6` | `gpt-5.6-luna`, High | `gpt-5.6-luna`, High |
| `6` | `gpt-6-luna`, High | `gpt-6-luna`, High |

The change affects new project sessions and agents started afterward. It cannot change a model already running in a chat or agent. The `5.6` profile has no separate Astra model in the available model set; exceptional Astra-only assignments must use an available model explicitly.
