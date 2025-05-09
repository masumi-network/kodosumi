import sys
import random
from pathlib import Path
import time
import asyncio
import uvicorn
from fastapi import Request, Response
from ray import serve

from kodosumi.core import ServeAPI, Launch, Tracer
from kodosumi.service.inout.forms import *
from kodosumi.service.inout.errors import InputsError
from kodosumi import response

app = ServeAPI()

model = Model(
    Markdown("# Test Flow"),
    Break(),
    Markdown("This is only a mock."),
    Break(),
    Submit("Submit"),
    Cancel("Cancel")
)

async def runner(inputs: dict, tracer: Tracer) -> dict:
    return response.Markdown(f"done")

mock = [
    ("Quantum Concierge", "A service that uses quantum computing to optimize travel itineraries for maximum efficiency and enjoyment."),
    ("EcoGuardian", "An innovative service that monitors and reduces your carbon footprint through personalized sustainability strategies."),
    ("HealthSync", "A holistic health management platform that synchronizes your medical data for seamless healthcare experiences."),
    ("MindMeld", "A cognitive enhancement service that uses AI to boost productivity and creativity."),
    ("Artisan Connect", "A platform that connects you with local artisans for bespoke handcrafted goods."),
    ("Gourmet Navigator", "A culinary exploration service that curates personalized dining experiences based on your taste preferences."),
    ("StyleCraft", "An AI-driven fashion advisory service that crafts personalized wardrobe recommendations."),
    ("HomeHarmony", "A smart home integration service that optimizes your living space for comfort and efficiency."),
    ("WellnessWave", "A personalized wellness program that adapts to your lifestyle for optimal health outcomes."),
    ("TechTutor", "An on-demand tech support service that provides expert guidance for all your digital needs."),
    ("FinanceFlow", "A financial planning service that uses AI to optimize your investment strategies."),
    ("TravelTapestry", "A bespoke travel planning service that weaves together unique experiences tailored to your interests."),
    ("EventEssence", "An event planning service that captures the essence of your vision and brings it to life."),
    ("PetPal", "A comprehensive pet care service that ensures your furry friends are happy and healthy."),
    ("GardenGenius", "A gardening advisory service that helps you cultivate a thriving and sustainable garden."),
    ("EduElevate", "An educational enhancement service that tailors learning experiences to your needs."),
    ("CareerCatalyst", "A career development platform that accelerates your professional growth through personalized strategies."),
    ("CultureCurator", "A cultural immersion service that connects you with local traditions and experiences."),
    ("FitnessFusion", "A dynamic fitness program that blends various exercise modalities for optimal results."),
    ("DreamDesigner", "A home design service that transforms your living space into a dream environment."),
    ("SafetySphere", "A personal security service that uses advanced technology to ensure your safety."),
    ("LegalLink", "A legal advisory service that connects you with expert legal counsel for your needs."),
    ("MemoryMosaic", "A digital archiving service that preserves your memories in a secure and accessible format."),
    ("SkillSculptor", "A skill development platform that molds your abilities for career advancement."),
    ("VisionVoyage", "A visionary travel service that crafts journeys to inspire and enlighten."),
    ("SoundScape", "An audio enhancement service that creates immersive sound experiences."),
    ("TasteTrail", "A culinary discovery service that leads you on a journey through global flavors."),
    ("EnergyEcho", "A renewable energy advisory service that amplifies your sustainability efforts."),
    ("SocialSync", "A social media management service that harmonizes your online presence."),
    ("CraftCove", "A creative workshop service that nurtures your artistic talents."),
    ("MindMap", "A mental wellness service that charts pathways to emotional balance."),
    ("TechTide", "A digital transformation service that rides the wave of technological innovation."),
    ("EcoEcho", "An environmental impact service that resonates with your sustainability goals."),
    ("HealthHaven", "A sanctuary for health that offers personalized care and wellness solutions."),
    ("StyleSage", "A fashion wisdom service that guides you through sartorial choices."),
    ("HomeHaven", "A domestic bliss service that creates a sanctuary in your living space."),
    ("WellnessWhisper", "A subtle wellness service that gently guides you to better health."),
    ("FinanceFinesse", "A financial finesse service that refines your investment strategies."),
    ("TravelTide", "A travel service that ebbs and flows with your wanderlust."),
    ("EventEclipse", "An event service that eclipses ordinary experiences with extraordinary ones."),
    ("PetPinnacle", "A pinnacle pet care service that elevates your pet's well-being."),
    ("GardenGlow", "A gardening service that illuminates your path to a flourishing garden."),
    ("EduEclipse", "An educational service that eclipses traditional learning with innovative methods."),
    ("CareerCompass", "A career service that navigates your path to professional success."),
    ("CultureCanvas", "A cultural service that paints a vivid picture of local traditions."),
    ("FitnessFlare", "A fitness service that ignites your passion for physical activity."),
    ("DreamDome", "A home service that encapsulates your dreams in a living space."),
    ("SafetyShield", "A security service that shields you from potential threats."),
    ("LegalLattice", "A legal service that weaves a network of expert counsel."),
    ("MemoryMirror", "A memory service that reflects your past in a digital format."),
]
for i, (summary, description) in enumerate(mock):
    @app.enter(f"/{i}", 
            model, 
            summary=summary, 
            description=description,
            tags=["Test"])
    async def enter(request: Request, inputs: dict) -> Response:
        return Launch(request, runner, inputs)

@serve.deployment
@serve.ingress(app)
class TestModel2: pass

fast_app2 = TestModel2.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example9:app", host="0.0.0.0", port=8010, reload=True)
