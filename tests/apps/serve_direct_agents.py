import asyncio
import uvicorn
import sys
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Union

from kodosumi import helper
from kodosumi.service.endpoint import (KODOSUMI_API, KODOSUMI_AUTHOR,
                               KODOSUMI_ORGANIZATION)
from kodosumi.serve import Launch, ServeAPI


from typing import List
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Request

api_specs = [
    {
        "summary": "User Authentication",
        "description": "Secure login, token generation, password reset, user verification, session management, access control, identity provider, OAuth, SSO, encryption, multi-factor authentication.",
        "tag": "Authentication"
    },
    {
        "summary": "Product Catalog",
        "description": "Browse products, search by category, filter by price, view details, add to cart, wishlist, inventory management, SKU, product images, reviews, ratings.",
        "tag": "E-commerce"
    },
    {
        "summary": "Weather Forecast",
        "description": "Current conditions, hourly forecast, daily forecast, temperature, humidity, wind speed, precipitation, UV index, sunrise, sunset, alerts, radar.",
        "tag": "Weather"
    },
    {
        "summary": "Payment Processing",
        "description": "Transaction initiation, card validation, fraud detection, currency conversion, receipt generation, refund processing, payment gateway, merchant account, PCI compliance, settlement.",
        "tag": "Finance"
    },
    {
        "summary": "Social Media Integration",
        "description": "Post creation, comment management, like system, share functionality, user mentions, hashtag tracking, feed generation, notification system, profile updates, direct messaging.",
        "tag": "Social"
    },
    {
        "summary": "Health Monitoring",
        "description": "Heart rate tracking, step counting, calorie calculation, sleep analysis, activity logging, goal setting, health metrics, wearable integration, data visualization, alerts.",
        "tag": "Health"
    },
    {
        "summary": "Travel Booking",
        "description": "Flight search, hotel reservation, car rental, itinerary management, price comparison, availability check, booking confirmation, cancellation policy, loyalty programs, travel insurance.",
        "tag": "Travel"
    },
    {
        "summary": "Content Management",
        "description": "Article creation, media upload, category assignment, tag management, SEO optimization, user roles, access permissions, version control, publishing workflow, analytics.",
        "tag": "CMS"
    },
    {
        "summary": "Inventory Tracking",
        "description": "Stock levels, reorder alerts, supplier management, SKU tracking, batch processing, warehouse location, shipment tracking, order fulfillment, demand forecasting, reporting.",
        "tag": "Logistics"
    },
    {
        "summary": "Customer Support",
        "description": "Ticket creation, status updates, priority setting, agent assignment, response templates, SLA tracking, customer feedback, knowledge base, chat integration, escalation.",
        "tag": "Support"
    },
    {
        "summary": "Machine Learning Model Deployment",
        "description": "Model training, hyperparameter tuning, dataset management, prediction API, model versioning, performance metrics, A/B testing, feature extraction, data preprocessing, deployment.",
        "tag": "AI"
    },
    {
        "summary": "Cryptocurrency Exchange",
        "description": "Market data, order book, trade execution, wallet management, deposit/withdrawal, security protocols, transaction history, price alerts, blockchain integration, compliance.",
        "tag": "Crypto"
    },
    {
        "summary": "Real Estate Listings",
        "description": "Property search, filter by location, price range, amenities, virtual tours, agent contact, mortgage calculator, neighborhood data, open house scheduling, market trends.",
        "tag": "Real Estate"
    },
    {
        "summary": "Educational Course Management",
        "description": "Course creation, enrollment process, syllabus upload, assignment submission, grading system, student feedback, instructor profiles, certification, online resources, discussion forums.",
        "tag": "Education"
    },
    {
        "summary": "Food Delivery Service",
        "description": "Restaurant search, menu browsing, order placement, delivery tracking, payment processing, customer reviews, loyalty rewards, promotional offers, dietary preferences, feedback.",
        "tag": "Food"
    },
    {
        "summary": "Vehicle Telemetry",
        "description": "Speed monitoring, fuel consumption, GPS tracking, engine diagnostics, maintenance alerts, driver behavior analysis, route optimization, accident detection, data logging, reporting.",
        "tag": "Automotive"
    },
    {
        "summary": "Event Management",
        "description": "Event creation, ticket sales, attendee registration, schedule planning, speaker profiles, venue details, sponsorship packages, promotional campaigns, feedback collection, analytics.",
        "tag": "Events"
    },
    {
        "summary": "Fitness Tracking",
        "description": "Workout logging, progress tracking, goal setting, exercise library, nutrition plans, community challenges, wearable integration, performance metrics, coaching tips, alerts.",
        "tag": "Fitness"
    },
    {
        "summary": "Home Automation",
        "description": "Device control, energy monitoring, security system integration, lighting schedules, thermostat settings, appliance management, voice commands, remote access, scene creation, alerts.",
        "tag": "IoT"
    },
    {
        "summary": "Music Streaming",
        "description": "Playlist creation, song search, artist profiles, album details, genre exploration, user recommendations, offline mode, social sharing, live radio, subscription plans.",
        "tag": "Music"
    },
    {
        "summary": "Language Translation",
        "description": "Text translation, speech recognition, language detection, dictionary lookup, phrase suggestions, grammar correction, regional dialects, API integration, user feedback, analytics.",
        "tag": "Language"
    },
    {
        "summary": "Online Marketplace",
        "description": "Seller registration, product listing, buyer search, transaction processing, review system, dispute resolution, shipping options, promotional tools, analytics dashboard, compliance.",
        "tag": "Marketplace"
    },
    {
        "summary": "Video Conferencing",
        "description": "Meeting scheduling, participant management, screen sharing, chat integration, recording options, virtual backgrounds, breakout rooms, security protocols, analytics, feedback.",
        "tag": "Communication"
    },
    {
        "summary": "Job Recruitment",
        "description": "Job posting, candidate search, application tracking, interview scheduling, resume parsing, employer branding, salary benchmarking, feedback collection, analytics, compliance.",
        "tag": "HR"
    },
    {
        "summary": "Library Management",
        "description": "Book cataloging, member registration, loan tracking, overdue alerts, reservation system, digital resources, author profiles, genre classification, event scheduling, analytics.",
        "tag": "Library"
    },
    {
        "summary": "Sports Statistics",
        "description": "Player profiles, team rankings, match results, live scores, historical data, performance analysis, injury reports, fan engagement, betting odds, analytics.",
        "tag": "Sports"
    },
    {
        "summary": "Environmental Monitoring",
        "description": "Air quality data, water levels, temperature readings, pollution alerts, wildlife tracking, sensor integration, data visualization, reporting tools, compliance, feedback.",
        "tag": "Environment"
    },
    {
        "summary": "Fashion Trends",
        "description": "Style guides, designer profiles, runway shows, seasonal collections, influencer collaborations, retail partnerships, consumer feedback, analytics, promotional campaigns, sustainability.",
        "tag": "Fashion"
    },
    {
        "summary": "News Aggregation",
        "description": "Article collection, source filtering, topic categorization, user preferences, alert system, multimedia integration, social sharing, analytics, feedback, compliance.",
        "tag": "News"
    },
    {
        "summary": "Personal Finance Management",
        "description": "Budget planning, expense tracking, investment advice, savings goals, credit score monitoring, tax calculations, financial reports, alerts, user feedback, compliance.",
        "tag": "Finance"
    },
    {
        "summary": "Public Transportation",
        "description": "Route planning, schedule updates, fare calculation, real-time tracking, service alerts, station information, accessibility options, user feedback, analytics, compliance.",
        "tag": "Transportation"
    },
    {
        "summary": "Gaming Leaderboards",
        "description": "Player rankings, score tracking, achievement badges, tournament scheduling, team management, user profiles, social sharing, analytics, feedback, compliance.",
        "tag": "Gaming"
    },
    {
        "summary": "Art Gallery Management",
        "description": "Exhibit planning, artist profiles, artwork cataloging, visitor registration, event scheduling, promotional campaigns, feedback collection, analytics, compliance, sustainability.",
        "tag": "Art"
    },
    {
        "summary": "Construction Project Management",
        "description": "Task scheduling, resource allocation, budget tracking, milestone setting, contractor management, site inspections, compliance checks, reporting tools, analytics, feedback.",
        "tag": "Construction"
    },
    {
        "summary": "Agricultural Monitoring",
        "description": "Crop tracking, soil analysis, weather forecasting, pest alerts, irrigation management, yield predictions, sensor integration, data visualization, reporting tools, compliance.",
        "tag": "Agriculture"
    },
    {
        "summary": "Mental Health Support",
        "description": "Therapy sessions, mood tracking, self-help resources, community forums, crisis intervention, user feedback, analytics, privacy protocols, compliance, sustainability.",
        "tag": "Health"
    },
    {
        "summary": "Retail Analytics",
        "description": "Sales tracking, customer segmentation, inventory management, promotional campaigns, feedback collection, reporting tools, user preferences, compliance, sustainability, analytics.",
        "tag": "Retail"
    },
    {
        "summary": "Energy Consumption",
        "description": "Usage tracking, cost calculation, efficiency analysis, renewable sources, grid integration, user feedback, reporting tools, compliance, sustainability, analytics.",
        "tag": "Energy"
    },
    {
        "summary": "Wildlife Conservation",
        "description": "Species tracking, habitat monitoring, threat alerts, community engagement, research collaboration, data visualization, reporting tools, compliance, sustainability, analytics.",
        "tag": "Conservation"
    },
    {
        "summary": "Space Exploration",
        "description": "Mission planning, satellite tracking, data collection, research collaboration, public engagement, reporting tools, compliance, sustainability, analytics, feedback.",
        "tag": "Space"
    },
    {
        "summary": "Disaster Response",
        "description": "Alert system, resource allocation, volunteer coordination, impact assessment, community engagement, reporting tools, compliance, sustainability, analytics, feedback.",
        "tag": "Emergency"
    },
    {
        "summary": "Urban Planning",
        "description": "Infrastructure development, zoning regulations, community engagement, environmental impact, resource allocation, reporting tools, compliance, sustainability, analytics, feedback.",
        "tag": "Urban"
    },
    {
        "summary": "Historical Archives",
        "description": "Document cataloging, artifact preservation, research collaboration, public access, community engagement, reporting tools, compliance, sustainability, analytics, feedback.",
        "tag": "History"
    },
    {
        "summary": "Culinary Recipes",
        "description": "Ingredient lists, cooking instructions, nutritional information, user reviews, dietary preferences, community engagement, reporting tools, compliance, sustainability, analytics.",
        "tag": "Food"
    },
    {
        "summary": "Fitness Challenges",
        "description": "Goal setting, progress tracking, community engagement, user feedback, reporting tools, compliance, sustainability, analytics, rewards system, wearable integration.",
        "tag": "Fitness"
    },
    {
        "summary": "Virtual Reality Experiences",
        "description": "Content creation, user interaction, feedback collection, reporting tools, compliance, sustainability, analytics, community engagement, hardware integration, performance metrics.",
        "tag": "VR"
    },
    {
        "summary": "Genealogy Research",
        "description": "Family tree creation, historical records, community engagement, user feedback, reporting tools, compliance, sustainability, analytics, collaboration, privacy protocols.",
        "tag": "Genealogy"
    },
    {
        "summary": "Robotics Control",
        "description": "Task scheduling, resource allocation, performance metrics, user feedback, reporting tools, compliance, sustainability, analytics, hardware integration, community engagement.",
        "tag": "Robotics"
    },
    {
        "summary": "Oceanography Data",
        "description": "Water quality monitoring, species tracking, environmental impact, research collaboration, community engagement, reporting tools, compliance, sustainability, analytics, feedback.",
        "tag": "Ocean"
    },
    {
        "summary": "Blockchain Transactions",
        "description": "Ledger management, smart contracts, user feedback, reporting tools, compliance, sustainability, analytics, community engagement, security protocols, performance metrics.",
        "tag": "Blockchain"
    }
]

api_specs.sort(key=lambda x: x["summary"])

def create_app(num_endpoints: int) -> FastAPI:
    global api_specs

    app = FastAPI()

    for i in range(num_endpoints):
        endpoint_path = f"/end-{i+1}"
        @app.get(endpoint_path, 
                 tags=[api_specs[i]["tag"]], 
                 summary=api_specs[i]["summary"],
                 description=api_specs[i]["description"],
                 openapi_extra={KODOSUMI_API: True})
        async def get_dynamic_endpoint(i=i) -> str:
            return f"end{i}"

    return app

def create20() -> FastAPI:
    return create_app(20)

def create50() -> FastAPI:
    return create_app(50)

if __name__ == "__main__":
    wd = str(Path(__file__).parent.parent.parent)
    sys.path.append(wd)
    uvicorn.run("tests.apps.serve_direct_agents:create50", 
                host="localhost", port=8002, reload=True, factory=True)
