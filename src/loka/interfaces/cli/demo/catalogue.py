"""DEMO ONLY — what the demo Yaoundé is made of.

Every owner has a number in +237 600 000 xxx, a range no operator hands out:
nothing Kora sends them reaches a real person, and ``_empty_all_data`` (which
only removes the two test numbers' data) leaves all of this in place.
"""

from __future__ import annotations

from typing import Any


def demo_phone(index: int) -> str:
    return f"+237600000{index:03d}"


# name, district, dishes (name, price, photographed)
RESTAURANTS: list[tuple[str, str, list[tuple[str, int, bool]]]] = [
    ("Chez Mama Ngono", "Étoug-ébé", [
        ("Ndolè crevettes", 2500, True), ("Poulet DG", 3500, True),
        ("Eru", 1500, False), ("Plantain mûr frit", 500, False),
    ]),
    ("Le Coin du Braisé", "Étoug-ébé", [
        ("Poisson braisé", 3000, True), ("Soya", 1000, True), ("Bâton de manioc", 200, False),
    ]),
    ("Restaurant La Bénédiction", "Biyem-Assi", [
        ("Koki", 1000, True), ("Okok sucré", 1500, False), ("Riz sauce arachide", 1500, True),
    ]),
    ("Mendong Food", "Mendong", [
        ("Achu soupe jaune", 2000, True), ("Poulet braisé", 3000, False),
    ]),
    ("Tantine Obili", "Obili", [
        ("Spaghetti omelette", 700, True), ("Haricot plantain", 500, False),
        ("Bouillon de pattes", 1500, False),
    ]),
    ("Saveurs de Melen", "Melen", [
        ("Mbongo tchobi", 2500, True), ("Kondrè", 2000, False), ("Riz sauté", 1500, False),
    ]),
    ("Le Bastos Lounge", "Bastos", [
        ("Poulet DG", 5000, True), ("Brochettes de bœuf", 3500, False),
        ("Salade avocat", 2500, False),
    ]),
    ("Chez Pauline Essos", "Éssos", [
        ("Ndolè viande", 2000, False), ("Sanga", 1500, True), ("Taro sauce jaune", 2000, False),
    ]),
]

# district, type, standing, rent, bedrooms, lodgings, words
LISTINGS: list[tuple[str, str, str, int, int, int, str]] = [
    ("Étoug-ébé", "ROOM", "MODERN", 30000, 1, 3, "Chambre moderne, douche interne, eau et lumière"),
    ("Étoug-ébé", "STUDIO", "MODERN", 60000, 1, 2, "Studio moderne carrelé, cuisine, forage"),
    ("Biyem-Assi", "APARTMENT", "MODERN", 120000, 2, 1, "Appartement 2 chambres salon, parking"),
    ("Biyem-Assi", "ROOM", "NON_MODERN", 15000, 1, 4, "Chambre simple, douche commune"),
    ("Mendong", "STUDIO", "MODERN", 50000, 1, 1, "Studio neuf, compteur personnel"),
    ("Obili", "ROOM", "MODERN", 25000, 1, 6, "Chambres d'étudiants près de l'université"),
    ("Melen", "STUDIO", "NON_MODERN", 35000, 1, 2, "Studio simple, proche marché Melen"),
    ("Bastos", "APARTMENT", "MODERN", 350000, 3, 1, "Appartement haut standing, gardien, groupe"),
    ("Bastos", "HOUSE", "MODERN", 600000, 4, 1, "Villa 4 chambres avec jardin"),
    ("Éssos", "APARTMENT", "MODERN", 90000, 2, 1, "Appartement 2 chambres, quartier calme"),
    ("Mvog-Mbi", "ROOM", "MODERN", 35000, 1, 2, "Chambre moderne près du carrefour"),
    ("Odza", "HOUSE", "NON_MODERN", 80000, 3, 1, "Maison 3 chambres, grande cour"),
]

DATING: list[dict[str, Any]] = [
    {"first_name": "Aïcha", "age": 24, "sex": "FEMME", "profession": "Étudiante en droit",
     "city": "Yaoundé", "looking": ("VILLE",),
     "description": "Je cherche un homme sérieux, attentionné, qui aime rire et parler de "
     "l'avenir. Pas pour s'amuser : une vraie relation, et pourquoi pas le mariage."},
    {"first_name": "Brenda", "age": 27, "sex": "FEMME", "profession": "Infirmière",
     "city": "Yaoundé", "looking": ("VILLE", "PAYS"),
     "description": "Je veux un homme travailleur, croyant, respectueux. J'aime les sorties "
     "tranquilles et la cuisine. Je cherche du stable."},
    {"first_name": "Christelle", "age": 30, "sex": "FEMME", "profession": "Commerçante",
     "city": "Yaoundé", "looking": ("PEU_IMPORTE",),
     "description": "Maman d'un garçon, je cherche un homme mûr et responsable qui accepte "
     "mon enfant. La sincérité avant tout."},
    {"first_name": "Diane", "age": 22, "sex": "FEMME", "profession": "Coiffeuse",
     "city": "Yaoundé", "looking": ("QUARTIER", "VILLE"),
     "description": "Je suis joyeuse et j'aime danser. Je cherche quelqu'un de drôle, "
     "généreux, qui prend soin de sa copine."},
    {"first_name": "Estelle", "age": 33, "sex": "FEMME", "profession": "Comptable",
     "city": "Yaoundé", "looking": ("PAYS", "ETRANGER"),
     "description": "Femme indépendante, je cherche un homme ambitieux et fidèle pour "
     "construire une famille. Les voyages me plaisent."},
    {"first_name": "Grâce", "age": 26, "sex": "FEMME", "profession": "Enseignante",
     "city": "Yaoundé", "looking": ("VILLE",),
     "description": "Calme et posée, j'aime la lecture et l'église. Je cherche un homme "
     "doux, patient, avec qui fonder un foyer."},
    {"first_name": "Junior", "age": 29, "sex": "HOMME", "profession": "Informaticien",
     "city": "Yaoundé", "looking": ("VILLE", "PAYS"),
     "description": "Je cherche une femme sérieuse et ambitieuse, pour une relation durable "
     "qui mène au mariage."},
    {"first_name": "Patrick", "age": 35, "sex": "HOMME", "profession": "Chauffeur",
     "city": "Yaoundé", "looking": ("PEU_IMPORTE",),
     "description": "Homme simple et travailleur, je veux une femme respectueuse pour "
     "partager ma vie."},
]

# type, district, found on, description (with the name read on it)
FOUND: list[tuple[str, str, str, str]] = [
    ("CNI", "Biyem-Assi", "hier", "CNI trouvée devant la pharmacie, au nom de NGONO Marie Claire"),
    ("CNI", "Mokolo", "avant-hier", "Carte d'identité dans un taxi, nom ATANGANA Paul Hervé"),
    ("PERMIS", "Mvog-Mbi", "la semaine passée", "Permis de conduire, nom MBARGA Jean"),
    ("CARTE_ETUDIANT", "Ngoa-Ékélé", "hier", "Carte d'étudiant Université Yaoundé I, FOTSO Linda"),
    ("PASSEPORT", "Bastos", "il y a 3 jours", "Passeport camerounais, nom EKANI Serge"),
]

# agency, lines (origin, destination, class, price, seats, layout, times)
AGENCIES: list[tuple[str, list[tuple[str, str, str, int, int, str, list[str]]]]] = [
    ("Express Voyages Démo", [
        ("Yaoundé", "Douala", "CLASSIQUE", 4000, 70, "2+2", ["06:00", "09:30", "14:00"]),
        ("Douala", "Yaoundé", "CLASSIQUE", 4000, 70, "2+2", ["06:00", "10:00", "15:00"]),
        ("Yaoundé", "Douala", "VIP", 7000, 39, "2+1", ["07:30", "16:00"]),
    ]),
    ("Confort Bus Démo", [
        ("Yaoundé", "Bafoussam", "CLASSIQUE", 5000, 70, "2+2", ["07:00", "13:00"]),
        ("Yaoundé", "Kribi", "CLASSIQUE", 4500, 70, "2+2", ["08:00"]),
        ("Yaoundé", "Bertoua", "VIP", 9000, 39, "2+1", ["06:30"]),
    ]),
]
