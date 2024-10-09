import requests
from PIL import Image
from io import BytesIO

def fetch_and_display_image(image_type, category):
    # Construct the API URL
    url = f"https://api.waifu.pics/{image_type}/{category}"

    # Make the API request
    response = requests.get(url)

    if response.status_code == 200:
        # Parse the JSON response
        data = response.json()
        
        # Get the image URL from the response
        image_url = data.get('url')
        
        if image_url:
            # Fetch the image
            image_response = requests.get(image_url)
            
            if image_response.status_code == 200:
                # Open the image using PIL
                image = Image.open(BytesIO(image_response.content))
                
                # Display the image
                image.show()
                
                print(f"Image URL: {image_url}")
            else:
                print(f"Failed to fetch the image. Status code: {image_response.status_code}")
        else:
            print("No image URL found in the API response.")
    else:
        print(f"API request failed. Status code: {response.status_code}")

# Example usage
fetch_and_display_image("nsfw", "waifu")