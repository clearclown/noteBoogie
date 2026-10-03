'use client'

import { useState } from 'react'

import { AppShell } from '@/components/layout/AppShell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AudiobooksTab } from '@/components/podcasts/AudiobooksTab'
import { EpisodesTab } from '@/components/podcasts/EpisodesTab'
import { TemplatesTab } from '@/components/podcasts/TemplatesTab'
import { Mic, LayoutTemplate, Headphones } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function PodcastsPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<'episodes' | 'audiobooks' | 'templates'>('episodes')

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="px-6 py-6 space-y-6">
          <header className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{t('podcasts.listTitle')}</h1>
            <p className="text-muted-foreground">
              {t('podcasts.listDesc')}
            </p>
          </header>


          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as 'episodes' | 'audiobooks' | 'templates')}
            className="space-y-6"
          >
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t('podcasts.chooseAView')}</p>
              <TabsList aria-label={t('common.accessibility.podcastViews')} className="w-full max-w-md flex-wrap sm:flex-nowrap">
                <TabsTrigger value="episodes" className="h-11 whitespace-nowrap px-1 text-xs sm:h-9 sm:px-4 sm:text-sm">
                  <Mic className="hidden h-4 w-4 sm:block" />
                  {t('podcasts.episodesTab')}
                </TabsTrigger>
                <TabsTrigger value="audiobooks" className="h-11 whitespace-nowrap px-1 text-xs sm:h-9 sm:px-4 sm:text-sm">
                  <Headphones className="hidden h-4 w-4 sm:block" />
                  {t('podcasts.audiobooksTab')}
                </TabsTrigger>
                <TabsTrigger value="templates" className="h-11 whitespace-nowrap px-1 text-xs sm:h-9 sm:px-4 sm:text-sm">
                  <LayoutTemplate className="hidden h-4 w-4 sm:block" />
                  {t('podcasts.templatesTab')}
                </TabsTrigger>
              </TabsList>
            </div>

            <TabsContent value="episodes">
              <EpisodesTab />
            </TabsContent>

            <TabsContent value="audiobooks">
              <AudiobooksTab />
            </TabsContent>

            <TabsContent value="templates">
              <TemplatesTab />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </AppShell>
  )
}
