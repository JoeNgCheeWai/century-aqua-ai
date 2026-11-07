import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

print("="*50)
print("TESTING ANTHROPIC API CONNECTION")
print("="*50)

api_key = os.getenv("ANTHROPIC_API_KEY")
print(f"\nAPI Key (first 15 chars): {api_key[:15] if api_key else 'NOT FOUND'}...")

model = os.getenv("SPECIALIST_MODEL", "claude-sonnet-5-5")
print(f"Testing model: {model}")

try:
    client = Anthropic()
    
    print("\nSending test request to Anthropic...")
    
    response = client.messages.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": "Hello"}]
    )
    
    print("\n✅ SUCCESS! API is working!")
    print(f"Response: {response.content[0].text}")
    print("="*50)
    
except Exception as e:
    print(f"\n❌ ERROR: {type(e).__name__}")
    print(f"Message: {str(e)}")
    print("="*50)
    
    if "authentication_error" in str(e):
        print("\nDIAGNOSIS: Your API key is invalid or expired.")
        print("FIX: Go to console.anthropic.com and create a new API key.")
    elif "not_found_error" in str(e):
        print("\nDIAGNOSIS: Your account doesn't have access to this model.")
        print("FIX: Go to console.anthropic.com > Settings > Billing and add a payment method.")
    elif "permission_error" in str(e):
        print("\nDIAGNOSIS: Your API key lacks permission for this model.")
        print("FIX: Check your key's scopes in console.anthropic.com.")
    else:
        print("\nDIAGNOSIS: Unknown error. Check the message above.")