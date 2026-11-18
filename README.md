# Expiration Date Reader

A Python Azure Function that reads expiration dates from product label images. It uploads the image to Azure Blob Storage, uses Azure Document Intelligence to extract the text, and searches that text for an expiration date.

## Run locally

1. Install Python and Azure Functions Core Tools.
2. Create an Azure Storage account with a Blob container named `cicc-blobs`. Create an Azure Document Intelligence resource as well.
3. In your local copy of `local.settings.json`, replace the placeholders with your own Azure settings. Keep real connection strings out of Git commits.
4. Sign in to Azure with an account that can upload blobs to the container and use the Document Intelligence resource.
5. From the project folder, install the dependencies and start the function.

   ```powershell
   pip install -r requirements.txt
   func start
   ```

## Try it

With the function running, open a second terminal and upload a product label image using the form field `file`.

```powershell
curl.exe -F "file=@label.jpg" http://localhost:7071/api/ciccFuncExpDate
```

Replace `label.jpg` with the path to your image.
