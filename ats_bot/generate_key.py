"""
generate_key.py — Run this ONCE to generate your encryption key
Copy the output into your .env file as ENCRYPT_KEY
"""
from cryptography.fernet import Fernet

key = Fernet.generate_key().decode()
print("\n✅ Your encryption key (copy this into .env):")
print(f"\nENCRYPT_KEY={key}\n")
print("⚠️  Keep this key safe! If lost, all stored cookies become unreadable.")
