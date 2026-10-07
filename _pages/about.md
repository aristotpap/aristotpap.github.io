---
layout: about
title: About
permalink: /
subtitle: PhD at the University of Oxford | J.P. Morgan AI Research Fellow | Clarendon Scholar

profile:
  align: right
  image: prof_pic.jpg
  image_circular: false # crops the image to make it circular
  more_info: >
    <div class="profile-links">
    <a href="mailto:aristotpap@gmail.com" title="Email" aria-label="Email"><i class="fa-solid fa-envelope"></i></a>
    <a href="https://www.linkedin.com/in/aristotelis-papatheodorou-2559ab127/" title="LinkedIn" aria-label="LinkedIn" target="_blank" rel="noopener"><i class="fa-brands fa-linkedin-in"></i></a>
    <a href="https://x.com/aristotpap" title="X (Twitter)" aria-label="X (Twitter)" target="_blank" rel="noopener"><i class="fa-brands fa-x-twitter"></i></a>
    <a href="https://github.com/aristotpap" title="GitHub" aria-label="GitHub" target="_blank" rel="noopener"><i class="fa-brands fa-github"></i></a>
    <a href="https://scholar.google.com/citations?user=08OrK4AAAAAJ" title="Google Scholar" aria-label="Google Scholar" target="_blank" rel="noopener"><i class="fa-brands fa-google-scholar"></i></a>
    <a href="/assets/pdf/cv.pdf" title="CV" aria-label="CV" target="_blank" rel="noopener"><i class="ai ai-cv"></i></a>
    </div>

selected_papers: false # includes a list of papers marked as "selected={true}"; off in favour of Recent Publications below
social: false # includes social icons at the bottom of the page

announcements:
  enabled: false # includes a list of news items
  scrollable: true # adds a vertical scroll bar if there are more than 3 news items
  limit: 5 # leave blank to include all the news in the `_news` folder

latest_posts:
  enabled: false
  scrollable: true # adds a vertical scroll bar if there are more than 3 new posts items
  limit: 3 # leave blank to include all the blog posts
---

I’m a PhD student at the [University of Oxford](https://www.ox.ac.uk/), working with [Prof. Ioannis Havoutis](https://ihavoutis.github.io/) at the [Oxford Robotics Institute](https://ori.ox.ac.uk/) on research at the intersection of AI, robotics, and classical mechanics. I’m a [Clarendon Scholar](https://www.ox.ac.uk/clarendon) and J.P. Morgan AI Research Fellow. During my PhD, I’ve also worked closely with the [RoMI Lab](https://www.romilab.org/), led by [Prof. Carlos Mastalli](https://cmastalli.github.io/), at the UK [National Robotarium](https://thenationalrobotarium.com/).

I see physics as a natural verifier for physical intelligence. Lean gave mathematical reasoning a checker that cannot be fooled, and has helped AI make progress on many open problems in mathematics. I think physical invariants can play a similar role in robotics. Robots have geometry, and their motion and interactions with the world are constrained by fundamental physical laws. My research explores how to build this structure into learning and control, with the aim of making robots more robust and useful.

Before Oxford, I earned my M.Eng. in Mechanical Engineering with honors from the [National Technical University of Athens](https://www.ntua.gr/en/), where I worked on legged robotics at the Control Systems Lab under [Prof. Evangelos Papadopoulos](https://nereus.mech.ntua.gr/). I spent several years developing distributed, real-time motion-control software and electronics, and have also worked as an R&D engineer in embedded systems, control, and AI.

<h2 style="clear: both">
  <a href="{{ '/publications/' | relative_url }}" style="color: inherit">Recent Publications</a>
</h2>

<!-- The three newest papers: papers.bib is kept newest first, and bin/update_publications.py adds new papers at the top -->
<div class="publications">

{% bibliography --group_by none --max 3 %}

</div>
