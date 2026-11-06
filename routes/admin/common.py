"""Shared constants and helpers for admin modules."""

PROPERTY_IMPORT_FORMAT = """
============================================================
 ESTACLE PROPERTY IMPORT FORMAT (CSV / Excel)
============================================================
 Header row (exact names):

title,description,price,property_type,status,listing_type,category,address,city,state,zip_code,bedrooms,bathrooms,area_sqft,carpet_area,floor,furnishing,parking,amenities,seller_name,seller_phone,monthly_rent,security_deposit,maintenance,per_sqft,year_built,facing,agent_email

 Rules:
  - title and price required
  - property_type: apartment|house|villa|plot|commercial|office|warehouse
  - status: available|under_offer|sold|rented
  - listing_type: sale|rent
  - category: residential|commercial|industrial
  - agent_email: optional, must match an existing agent account
  - numeric fields can be empty

 Example row:
3BHK Sea View,Corner unit with balcony,12500000,apartment,available,sale,residential,Marine Drive,Mumbai,MH,400001,3,2,1450,1200,12,furnished,2 covered,"Pool,Gym",Rajesh,+9198xxxxxx,0,0,5000,8620,2019,East,agent@estacle.com
============================================================
"""

USER_IMPORT_FORMAT = """
============================================================
 ESTACLE USER IMPORT FORMAT (CSV / Excel)
============================================================
 Required columns (header row must match exactly):

   full_name,email,role,phone,password

 Rules:
   - role must be one of: admin | agent | customer
   - password min 6 characters
   - email must be unique
   - phone is optional (can be empty)

 Example CSV:
------------------------------------------------------------
full_name,email,role,phone,password
John Agent,john.agent@estacle.com,agent,+1-555-0101,agent123
Sarah Customer,sarah@estacle.com,customer,+1-555-0102,customer123
Admin Two,admin2@estacle.com,admin,,admin12345
------------------------------------------------------------
 Excel: same headers in row 1, data from row 2. Sheet 1 only.
============================================================
"""
