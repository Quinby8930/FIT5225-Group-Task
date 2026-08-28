export const HABITAT_SLIDES = Object.freeze([
  {
    id: "rainforest",
    title: "Rainforest observation",
    description: "Canopy cameras document wildlife beneath the Pacific Northwest's temperate forest cover.",
    image: "images/habitats/pacific-rainforest.webp",
    alt: "Mist-shrouded trees in the Hoh Rain Forest, Olympic National Park",
    credit: "Hoh Rain Forest — NPS / Jon Preston, public domain.",
  },
  {
    id: "coast",
    title: "Coastal observation",
    description: "Underwater video systems extend the archive from coastal sanctuaries into marine habitats.",
    image: "images/habitats/pacific-coast.webp",
    alt: "Looking upward through giant kelp in Channel Islands National Marine Sanctuary",
    credit: "Channel Islands kelp forest — NOAA Photo Library, CC BY 2.0.",
  },
  {
    id: "desert",
    title: "Desert observation",
    description: "Motion-triggered field cameras capture nocturnal activity around arid watering sites.",
    image: "images/habitats/california-desert.webp",
    alt: "Joshua trees and boulders beneath clouds in Joshua Tree National Park",
    credit: "Joshua Tree National Park — NPS / Paul Martinez, public domain.",
  },
]);

export const SUGGESTED_SPECIES = Object.freeze([
  {
    id: "wombat",
    name: "Common wombat",
    query: "wombat",
    description: "A robust nocturnal marsupial represented in the supplied model labels.",
    image: "images/species/common-wombat.webp",
    alt: "Common wombat on Maria Island, Tasmania",
    credit: "Ena Music / Wikimedia Commons, CC BY-SA 4.0.",
  },
  {
    id: "dingo",
    name: "Dingo",
    query: "dingo",
    description: "A free-ranging canid and one of the assignment's example species queries.",
    image: "images/species/dingo.webp",
    alt: "Dingo standing on K'gari, Queensland",
    credit: "Sam Fraser-Smith, CC BY 2.0.",
  },
  {
    id: "cassowary",
    name: "Southern cassowary",
    query: "cassowary",
    description: "A large rainforest bird included in the supplied inference label set.",
    image: "images/species/southern-cassowary.webp",
    alt: "Southern cassowary in North Queensland tropical rainforest",
    credit: "Dave Kimble / Wikimedia Commons, public domain.",
  },
]);

export function carouselIndexAfter(currentIndex, delta, slideCount) {
  if (!Number.isInteger(slideCount) || slideCount <= 0) return 0;
  if (!Number.isInteger(currentIndex)) return 0;
  const current = currentIndex;
  const step = Number.isInteger(delta) ? delta : 0;
  return ((current + step) % slideCount + slideCount) % slideCount;
}
