# ComfyUI GPU Monitor

Painel fixo no canto da interface do ComfyUI com **processamento da CPU e das GPUs em porcentagem e barras**, **RAM usada/total** e **todas as GPUs suportadas**, nome, VRAM usada/total em GiB, porcentagem e barra de uso. Funciona também quando nenhum workflow está em execução. Não precisa colocar um node no grafo.

## Instalação

Coloque a pasta `ComfyUI-System-Monitor` em `ComfyUI/custom_nodes`, reinicie o ComfyUI e recarregue a página do navegador. Não há dependências Python adicionais: utiliza a biblioteca padrão, psutil, aiohttp e o frontend já fornecidos pelo ComfyUI.

Nesta máquina, a pasta é:

```text
D:\new-comfyui\Intel2\ComfyUI\custom_nodes\ComfyUI-System-Monitor
```

## Uso

O painel aparece automaticamente no canto inferior esquerdo e atualiza a cada **2 segundos**. Use **− / +** para recolher ou expandir. Em **Configurações → GPU Monitor**, é possível ocultar o painel, escolher um dos quatro cantos e ajustar a atualização de **0,1 a 5 segundos**, em passos de 0,1 s. O slider na parte inferior do painel controla o mesmo ajuste e salva a preferência. O intervalo real depende também do tempo de consulta dos contadores do sistema.

Verde indica uso abaixo de 75%, amarelo a partir de 75% e vermelho a partir de 90%. Abas em segundo plano e o painel desativado suspendem novas consultas. Falhas de conexão deixam os últimos valores esmaecidos e mostram uma mensagem até a próxima leitura.

## GPUs e métricas

- **Intel, AMD e NVIDIA, Windows:** identifica todas as placas físicas pelo DXGI e consulta o uso global de VRAM dedicada pelos contadores GPU Adapter Memory do Windows. Não requer nvidia-smi. O total utiliza um contador de 64 bits, inclusive para placas com mais de 4 GiB.
- **CPU:** mostra o uso global de processamento, com amostragem de 100 ms.
- **GPU (processamento):** no Windows, soma os processos por engine e mostra a engine mais ocupada; no Linux, usa `utilization.gpu` do NVIDIA ou `gpu_busy_percent` do DRM quando disponível. Leituras indisponíveis aparecem como **—**.
- **RAM:** mostra memória física usada/total, considerando usada a diferença entre total e disponível.
- **NVIDIA, Linux:** consulta todas as placas retornadas por `nvidia-smi`, inclusive GPUs que não foram selecionadas pelo ComfyUI ou ocultas por `CUDA_VISIBLE_DEVICES`. O executável precisa estar no PATH; normalmente acompanha o driver NVIDIA.
- **AMD e outros drivers DRM, Linux:** consulta as placas que expõem `mem_info_vram_used` e `mem_info_vram_total` em `/sys/class/drm`. Quando o driver não informa o nome comercial, exibe a identificação `cardN`.
- No Linux, Intel depende dos mesmos contadores DRM; drivers que não os expõem não fornecem métricas. Apple Silicon não é monitorado. Memória compartilhada com a RAM não é somada à VRAM dedicada; GPUs integradas podem ter pouca ou nenhuma VRAM dedicada.
- Os valores são os contadores de uso global do driver, incluindo outros programas, e não apenas memória alocada pelo PyTorch. Contadores indisponíveis aparecem como **—**, nunca como zero fictício. 1 GiB = 1024³ bytes.

O monitor não inicializa contextos CUDA, não aloca tensores na GPU e não executa comandos para liberar VRAM. As consultas usam timeout, rodam fora do loop HTTP e compartilham um cache de 0,1 segundo entre clientes. Não acessa serviços externos, não envia telemetria e não baixa arquivos.

## API e testes

`GET /gpu-monitor/status` retorna `cpu`, `ram`, `gpus`, `errors` e `timestamp`. `ram` inclui `used_bytes` e `total_bytes`. `cpu` inclui `utilization_percent`. Cada GPU inclui `id`, `index`, `name`, `vendor`, `used_bytes`, `total_bytes` e `utilization_percent`.

Na pasta do ComfyUI:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s '.\custom_nodes\ComfyUI-System-Monitor\tests' -v
node --check '.\custom_nodes\ComfyUI-System-Monitor\web\gpu-monitor.js'
```

Referências: [API de extensões do ComfyUI](https://docs.comfy.org/custom-nodes/js/javascript_overview), [contadores NVIDIA-SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html).


A identificação e o total de VRAM no Windows seguem [DXGI_ADAPTER_DESC1](https://learn.microsoft.com/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1).
