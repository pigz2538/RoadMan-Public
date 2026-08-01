import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import TripPlanView from '../views/TripPlanView.vue'
import OpsView from '../views/OpsView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/home', component: HomeView },
    { path: '/trips/:tripId/plan', component: TripPlanView },
    { path: '/ops', component: OpsView },
  ],
})
