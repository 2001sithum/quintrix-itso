# Azure Container Apps Deployment

This directory contains the deployment configuration for hosting Quintrix ITSO on **Azure Container Apps** with an **Azure Container Registry (ACR)**.

## Prerequisites

1. [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed and logged in (`az login`).
2. Docker running locally.

## Deployment

Run the deploy script:

```bash
./deploy/azure/deploy.sh
```

### Optional Environment Variables

- `AZURE_RG`: Resource Group name (default: `quintrix-itso-rg`)
- `AZURE_LOCATION`: Azure region (default: `centralindia`)
- `AZURE_ACR`: Azure Container Registry name (default: `quintrixitsoacr`)
- `AZURE_ENV`: Container Apps Environment name (default: `quintrix-itso-env`)
- `AZURE_APP`: Container App name (default: `quintrix-itso-app`)
- `CPU`: Number of vCPUs (default: `2.0`)
- `MEMORY`: Memory size (default: `4.0Gi`)
