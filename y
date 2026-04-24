import React, { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Badge, Button, Checkbox, Loader, Table, Text, TextInput, Select, Progress } from '@mantine/core';
import useWebSocket, { ReadyState } from 'react-use-websocket';
import { notifications } from '@mantine/notifications';

import { cn } from '@/lib/utils';
import { useDiscoverUrls } from '@/apis/queries/scrapper.queries';
import { useKnowledgeBases, useIngestScrapedResults } from '@/apis/queries/knowledge-base.queries';

const API_URL = import.meta.env.VITE_API_URL_BASE || 'localhost:8000';

// Fix for blank screen: Safely convert http/https to ws/wss scheme to avoid WebSocket crashes
const WS_URL = API_URL.startsWith('http')
  ? API_URL.replace(/^http/, 'ws') + '/api/scrapper/extract-attributes'
  : `ws://${API_URL}/api/scrapper/extract-attributes`;

const WebIngester = ({ className }: { className?: string }) => {
  const [urls, setUrls] = useState<string[]>([]);
  const [selectedUrls, setSelectedUrls] = useState<string[]>([]);
  const [selectedKb, setSelectedKb] = useState<string | null>(null);
  const [results, setResults] = useState<any[]>([]);
  const [isExtracting, setIsExtracting] = useState(false);
  const [mode, setMode] = useState<'initial' | 'discovery' | 'extraction'>('initial');

  const { register, handleSubmit: handleFormSubmit } = useForm({
    defaultValues: { url: 'https://' }
  });

  const discoverUrls = useDiscoverUrls();
  const ingestResults = useIngestScrapedResults();
  const knowledgeBasesQuery = useKnowledgeBases({ page: 0, limit: 100 });

  const kbData = useMemo(() => {
    const response = knowledgeBasesQuery.data as any;
    const items = response?.data || (Array.isArray(response) ? response : []);
    return items.map((kb: any) => ({
      value: String(kb.id),
      label: String(kb.name),
    }));
  }, [knowledgeBasesQuery.data]);

  const { readyState, sendJsonMessage } = useWebSocket(WS_URL, {
    share: false,
    shouldReconnect: () => false,
    onMessage: (event) => {
      try {
        const message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (message?.url) {
          setResults((prev) => {
            if (prev.some(r => r.url === message.url)) return prev;
            return [...prev, message];
          });
        }
      } catch (err) {
        console.error('WS Error:', err);
      }
    },
    onClose: () => setIsExtracting(false),
  }, selectedUrls.length > 0);

  const statusColor: Record<ReadyState, string> = {
    [ReadyState.CONNECTING]: 'blue',
    [ReadyState.OPEN]: 'green',
    [ReadyState.CLOSING]: 'yellow',
    [ReadyState.CLOSED]: 'red',
    [ReadyState.UNINSTANTIATED]: 'gray',
  };

  const onDiscover = handleFormSubmit((data) => {
    setUrls([]);
    setResults([]);
    discoverUrls.mutate(data.url, {
      onSuccess: (data) => {
        setUrls(data || []);
        setMode('discovery');
        setSelectedUrls([]);
      }
    });
  });

  const onExtract = () => {
    if (!selectedKb) return notifications.show({ color: 'red', message: 'Select KB' });
    setResults([]);
    setIsExtracting(true);
    setMode('extraction');
    sendJsonMessage({ urls: selectedUrls });
  };

  const onIngest = () => {
    if (!selectedKb) return;

    // Force strictly typing to ensure it matches the hook's requirements
    const validResults = results
      .filter((r) => r.url && r.result)
      .map((r) => ({ url: String(r.url), result: String(r.result) }));

    if (validResults.length === 0) {
      notifications.show({ color: 'red', message: 'No valid results to save' });
      return;
    }

    ingestResults.mutate({
      id: selectedKb,
      results: validResults
    }, {
      onSuccess: () => {
        notifications.show({ color: 'green', message: 'Ingested successfully!' });
        setResults([]);
        setMode('initial');
      },
      onError: (err: Error) => {
        notifications.show({ 
          color: 'red', 
          title: 'Save Failed',
          message: err.message || 'Error saving to knowledge base'
        });
      }
    });
  };

  return (
    <div className={cn(className, 'p-4')}>
      <form onSubmit={onDiscover} className="flex gap-2 mb-6">
        <TextInput {...register('url')} placeholder="URL to scrape" className="flex-1" radius="md" />
        <Button type="submit" loading={discoverUrls.isPending} radius="md">Discover</Button>
      </form>

      {discoverUrls.isPending && <Loader size="sm" className="mb-4" />}

      {mode !== 'initial' && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-4 items-center">
            <Text size="sm">Found: {urls.length}</Text>
            <Select 
              placeholder="Target Knowledge Base" 
              data={kbData} 
              value={selectedKb} 
              onChange={setSelectedKb}
              className="w-64"
            />
            <Button 
              onClick={onExtract} 
              disabled={selectedUrls.length === 0 || isExtracting || !selectedKb}
              radius="md"
            >
              {isExtracting ? 'Extracting...' : `Scrape & Ingest (${selectedUrls.length})`}
            </Button>
            {results.length > 0 && !isExtracting && (
                <Button color="green" onClick={onIngest} loading={ingestResults.isPending} radius="md">
                    Save to KB
                </Button>
            )}
            {mode === 'extraction' && <Badge color={statusColor[readyState] || 'gray'}>{ReadyState[readyState]}</Badge>}
          </div>

          {isExtracting && (
            <Progress 
              value={selectedUrls.length > 0 ? (results.length / selectedUrls.length) * 100 : 0} 
              animated 
            />
          )}

          {mode === 'discovery' && (
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th><Checkbox onChange={(e) => setSelectedUrls(e.target.checked ? urls : [])} /></Table.Th>
                  <Table.Th>URL</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {urls.map(url => (
                  <Table.Tr key={url}>
                    <Table.Td>
                      <Checkbox 
                        checked={selectedUrls.includes(url)} 
                        onChange={(e) => setSelectedUrls(prev => e.target.checked ? [...prev, url] : prev.filter(u => u !== url))} 
                      />
                    </Table.Td>
                    <Table.Td>{url}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}

          {results.length > 0 && (
            <Table>
              <Table.Thead>
                <Table.Tr><Table.Th>URL</Table.Th><Table.Th>Status</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {results.map(r => (
                  <Table.Tr key={r.url}>
                    <Table.Td>{r.url}</Table.Td>
                    <Table.Td><Badge color="green">Extracted</Badge></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </div>
      )}
    </div>
  );
};

export default WebIngester;


export const useIngestScrapedResults = () => {
  const queryClient = useQueryClient();

  return useMutation<IKnowledgeBase, Error, { id: string; results: Array<{ url: string; result: string }> }>({
    mutationFn: ({ id, results }) => ingestScrapedResults(id, results),
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: ['knowledge-base', res.id] });
      await queryClient.invalidateQueries({ queryKey: ['knowledge-base', res.slug] });
      await queryClient.invalidateQueries({ queryKey: ['knowledge-base-files', res.id] });
    },
  });
};

export const ingestScrapedResults = async (id: string, results: Array<{ url: string; result: string }>) => {
  return http.post<IKnowledgeBase>(`/api/knowledge-base/${id}/ingest-scraped`, { results });
};
