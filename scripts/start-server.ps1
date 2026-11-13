# Starts llama-server tuned for a 30-35B MoE (Qwen3.6) on an 8 GB GPU + 32 GB RAM laptop.
# Usage:  .\start-server.ps1                     (auto-finds a .gguf in .\models)
#         .\start-server.ps1 -CpuMoe 30 -Ctx 131072
param(
    [string]$Model = "",          # path to .gguf (first shard if split)
    [int]$Ctx = 65536,            # context window in tokens
    [int]$CpuMoe = 99,            # MoE layers whose experts stay in RAM. 99 = all (safe start). Lower it to use more VRAM.
    [string]$CacheType = "q8_0",  # KV cache precision: f16 | q8_0 | q4_0
    [int]$Threads = 8,            # 14900HX has 8 P-cores; E-cores tend to slow things down
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$KitDir = $PSScriptRoot

if (-not $Model) {
    $found = Get-ChildItem -Path (Join-Path $KitDir "models") -Filter *.gguf -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch "mmproj" } | Sort-Object FullName | Select-Object -First 1
    if (-not $found) { throw "No .gguf found in $KitDir\models. Put your model there or pass -Model <path>." }
    $Model = $found.FullName
}

$server = (Get-Command llama-server -ErrorAction SilentlyContinue).Source
if (-not $server) { $server = Join-Path $KitDir "llama.cpp\llama-server.exe" }
if (-not (Test-Path $server)) { throw "llama-server not found. Unzip the llama.cpp CUDA release into $KitDir\llama.cpp (see README)." }

Write-Host "Model:   $Model"
Write-Host "Context: $Ctx  | experts-on-CPU layers: $CpuMoe | KV cache: $CacheType"
Write-Host "Server:  http://127.0.0.1:$Port  (Ctrl+C to stop)"

& $server `
    -m $Model --alias qwen3.6 `
    -c $Ctx `
    -ngl 99 --n-cpu-moe $CpuMoe `
    -fa on --cache-type-k $CacheType --cache-type-v $CacheType `
    -t $Threads `
    --jinja `
    --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0 `
    --host 127.0.0.1 --port $Port
